// Task 20 Phase 0: does a tree of draft candidates accept more often than a
// single greedy pick, and by enough to be worth the verification machinery?
//
// Method (TASK_20_TREE_SPECULATIVE_PLAN.md Phase 0): run the TARGET greedily
// to produce the reference continuation. At every step ask the DRAFT, given
// exactly the same context, for its top-N candidates, and record where the
// target's actual next token lands: draft's top-1 (what linear speculation
// accepts today), top-2, top-3 (what a tree of that width would accept), or
// nowhere.
//
// Both models see identical context, so this measures the draft's agreement
// with the target and nothing else -- no sampling noise, no pipeline, no
// network.
//
// Build: see README.md in this directory.

#include "llama.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

static const char * PROMPTS[] = {
    "Explain what a neural network is.",
    "Write a short paragraph about the sea.",
    "List three reasons why testing matters.",
    "What is the capital of France, and why is it famous?",
    "Describe how a bicycle works to a child.",
    "Summarise the plot of a detective story in a few sentences.",
};

static const int MAX_K = 4;   // widest tree we report
static const int N_GEN = 48;  // tokens of reference continuation per prompt

static double pct_of(long num, long den) {
    return den ? 100.0 * (double) num / (double) den : 0.0;
}

struct model_ctx {
    llama_model *   model = nullptr;
    llama_context * ctx   = nullptr;
};

static bool load(const char * path, int n_ctx, model_ctx & out) {
    llama_model_params mp = llama_model_default_params();
    out.model = llama_model_load_from_file(path, mp);
    if (!out.model) {
        fprintf(stderr, "failed to load %s\n", path);
        return false;
    }
    llama_context_params cp = llama_context_default_params();
    cp.n_ctx   = n_ctx;
    cp.n_batch = n_ctx;
    cp.no_perf = true;
    out.ctx = llama_init_from_model(out.model, cp);
    if (!out.ctx) {
        fprintf(stderr, "failed to create context for %s\n", path);
        return false;
    }
    return true;
}

// Decode `tokens` starting at `pos0` and return the logits of the last one.
static const float * decode_and_get_logits(llama_context * ctx,
        const std::vector<llama_token> & tokens, int pos0) {
    llama_batch batch = llama_batch_init((int32_t) tokens.size(), 0, 1);
    for (size_t i = 0; i < tokens.size(); ++i) {
        batch.token[i]     = tokens[i];
        batch.pos[i]       = pos0 + (int32_t) i;
        batch.n_seq_id[i]  = 1;
        batch.seq_id[i][0] = 0;
        batch.logits[i]    = (int8_t) (i + 1 == tokens.size());
    }
    batch.n_tokens = (int32_t) tokens.size();
    const int rc = llama_decode(ctx, batch);
    llama_batch_free(batch);
    if (rc != 0) {
        return nullptr;
    }
    return llama_get_logits_ith(ctx, -1);
}

// Indices of the top-k logits, best first.
static std::vector<llama_token> top_k(const float * logits, int n_vocab, int k) {
    std::vector<llama_token> idx(n_vocab);
    for (int i = 0; i < n_vocab; ++i) {
        idx[i] = i;
    }
    std::partial_sort(idx.begin(), idx.begin() + k, idx.end(),
            [logits](llama_token a, llama_token b) { return logits[a] > logits[b]; });
    idx.resize(k);
    return idx;
}

int main(int argc, char ** argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s TARGET.gguf DRAFT.gguf\n", argv[0]);
        return 1;
    }
    llama_backend_init();

    model_ctx target, draft;
    if (!load(argv[1], 2048, target) || !load(argv[2], 2048, draft)) {
        return 1;
    }

    const llama_vocab * tvocab = llama_model_get_vocab(target.model);
    const llama_vocab * dvocab = llama_model_get_vocab(draft.model);
    const int n_vocab_t = llama_vocab_n_tokens(tvocab);
    const int n_vocab_d = llama_vocab_n_tokens(dvocab);
    if (n_vocab_t != n_vocab_d) {
        // Different vocabularies make candidate ids incomparable -- a pair
        // like this cannot be used for speculation at all.
        fprintf(stderr, "vocab mismatch: target %d, draft %d -- not a usable pair\n",
                n_vocab_t, n_vocab_d);
        return 1;
    }

    long steps = 0;
    long hit_at[MAX_K] = { 0 };   // hit_at[i] = target token was draft's rank i
    long miss = 0;

    const int n_prompts = (int) (sizeof(PROMPTS) / sizeof(PROMPTS[0]));
    for (int p = 0; p < n_prompts; ++p) {
        // Per-prompt baseline, so one talkative prompt can't carry the average.
        const long steps0 = steps;
        long h0[MAX_K];
        for (int i = 0; i < MAX_K; ++i) {
            h0[i] = hit_at[i];
        }
        std::vector<llama_token> toks(512);
        const int n = llama_tokenize(tvocab, PROMPTS[p], (int32_t) strlen(PROMPTS[p]),
                toks.data(), (int32_t) toks.size(), true, false);
        if (n <= 0) {
            continue;
        }
        toks.resize(n);

        llama_memory_clear(llama_get_memory(target.ctx), true);
        llama_memory_clear(llama_get_memory(draft.ctx),  true);

        // Prime both with the prompt.
        const float * tlog = decode_and_get_logits(target.ctx, toks, 0);
        const float * dlog = decode_and_get_logits(draft.ctx,  toks, 0);
        if (!tlog || !dlog) {
            fprintf(stderr, "prompt %d: prefill failed\n", p);
            continue;
        }

        int pos = n;
        for (int step = 0; step < N_GEN; ++step) {
            // What the target actually does next (greedy = the reference).
            const std::vector<llama_token> tbest = top_k(tlog, n_vocab_t, 1);
            const llama_token next = tbest[0];

            // Where that token sits in the draft's ranking at the same point.
            const std::vector<llama_token> dtop = top_k(dlog, n_vocab_d, MAX_K);
            int rank = -1;
            for (int i = 0; i < MAX_K; ++i) {
                if (dtop[i] == next) {
                    rank = i;
                    break;
                }
            }
            if (rank >= 0) {
                hit_at[rank]++;
            } else {
                miss++;
            }
            ++steps;

            if (llama_vocab_is_eog(tvocab, next)) {
                break;
            }

            // Feed the target's choice to both, so the draft is never judged
            // on a context the target did not actually produce.
            const std::vector<llama_token> one = { next };
            tlog = decode_and_get_logits(target.ctx, one, pos);
            dlog = decode_and_get_logits(draft.ctx,  one, pos);
            if (!tlog || !dlog) {
                break;
            }
            ++pos;
        }
        printf("  prompt %d/%d: steps %ld  top-1 %5.2f%%  top-2 %5.2f%%  top-3 %5.2f%%\n",
                p + 1, n_prompts, steps - steps0,
                pct_of(hit_at[0] - h0[0], steps - steps0),
                pct_of(hit_at[0] - h0[0] + hit_at[1] - h0[1], steps - steps0),
                pct_of(hit_at[0] - h0[0] + hit_at[1] - h0[1] + hit_at[2] - h0[2], steps - steps0));
        fflush(stdout);
    }

    printf("\n=== Task 20 Phase 0: top-k draft acceptance ===\n");
    printf("steps measured: %ld\n\n", steps);

    long cum = 0;
    double e_linear = 0.0;
    for (int k = 0; k < MAX_K; ++k) {
        cum += hit_at[k];
        const double pct = steps ? 100.0 * (double) cum / (double) steps : 0.0;
        if (k == 0) {
            e_linear = pct;
        }
        printf("  target token within draft top-%d : %6.2f%%  (rank %d alone: %5.2f%%)\n",
                k + 1, pct, k, steps ? 100.0 * (double) hit_at[k] / (double) steps : 0.0);
    }
    printf("  not in top-%d at all             : %6.2f%%\n",
            MAX_K, steps ? 100.0 * (double) miss / (double) steps : 0.0);

    // The decision table in the plan is about E -- expected accepted tokens
    // per verify wave -- not about the per-step acceptance rate above. With
    // draft depth k and per-step acceptance p, a wave yields the verify's own
    // token plus however many drafted ones survive: E = 1 + sum_{i=1..k} p^i.
    // The two ratios differ a lot (a small rise in p compounds over the wave),
    // so feeding the raw acceptance ratio into the table would understate it.
    const int K_DRAFT = 4;   // k currently used in production (speculative_draft_k)
    auto expected_tokens = [](double p, int k) {
        double e = 1.0, term = 1.0;
        for (int i = 0; i < k; ++i) {
            term *= p;
            e += term;
        }
        return e;
    };

    const double p1 = (double) hit_at[0] / (double) steps;
    const double p2 = (double) (hit_at[0] + hit_at[1]) / (double) steps;
    const double p3 = (double) (hit_at[0] + hit_at[1] + hit_at[2]) / (double) steps;

    const double E1 = expected_tokens(p1, K_DRAFT);
    const double E2 = expected_tokens(p2, K_DRAFT);
    const double E3 = expected_tokens(p3, K_DRAFT);

    printf("\n--- expected accepted tokens per wave (k=%d) ---\n", K_DRAFT);
    printf("E_linear         = %.4f\n", E1);
    printf("E_tree(width 2)  = %.4f   E-ratio %.3f\n", E2, E2 / E1);
    printf("E_tree(width 3)  = %.4f   E-ratio %.3f\n", E3, E3 / E1);

    // throughput_ratio = E_ratio / T_ratio must clear the plan's bands, so
    // each band becomes a ceiling on how much more the wider verify wave may
    // cost. That ceiling is the one number a Phase 1 prototype has to measure.
    printf("\n--- break-even verify cost (T_tree/T_linear must stay BELOW) ---\n");
    printf("            to reach 1.10 (prototype)   to reach 1.30 (full go)\n");
    printf("  width 2        %.3f                       %.3f\n",
            (E2 / E1) / 1.10, (E2 / E1) / 1.30);
    printf("  width 3        %.3f                       %.3f\n",
            (E3 / E1) / 1.10, (E3 / E1) / 1.30);
    printf("\nGo/no-go needs these combined with a measured verify-cost ratio,\n"
           "per TASK_20_TREE_SPECULATIVE_PLAN.md 0.5 -- acceptance alone decides nothing.\n");

    llama_free(draft.ctx);
    llama_free(target.ctx);
    llama_model_free(draft.model);
    llama_model_free(target.model);
    llama_backend_free();
    return 0;
}
