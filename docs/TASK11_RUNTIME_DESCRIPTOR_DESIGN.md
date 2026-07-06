# Task 11.0 — Runtime Descriptor Architecture Refactor

**Status:** Design first  
**Scope:** Architecture document only  
**Implementation freeze:** Existing runtime, planner, orchestrator, install planner, node-agent, and model-loader code must not be changed until this document is accepted.

---

## 0. Executive Summary

Task 11.0 defines the final architecture for moving from a GGUF-first and layer-index-first distributed runtime to a Runtime Descriptor driven runtime.

The central change is ownership of model-specific knowledge. Today, model semantics can leak through several places: planner decisions, orchestrator setup paths, node-agent role substitution, install placement, worker materialization, and runtime loader branches. The target architecture forbids that. After the migration, all model-family knowledge lives in the Runtime Descriptor. Every other subsystem consumes descriptor facts and performs generic scheduling, validation, placement, transport, or execution.

The desired invariant is simple:

```
Architecture Descriptor
  -> Runtime Descriptor
      -> Runtime Services
      -> Semantic Resources
      -> Execution Graph
          -> Planner placement
          -> Install placement
          -> Orchestrator configuration
          -> Node-agent binding
          -> Runtime execution
```

No runtime role is inferred from `entry`. No embedding service is inferred from `layer_start = 0`. No output service is inferred from `layer_end = n_layer`. No planner or orchestrator code branches on `llama`, `qwen`, `gemma`, `phi`, `smollm`, or `deepseek`. Those names may appear only while constructing or validating architecture descriptors and while reporting user-facing model metadata.

This document intentionally does not prescribe code. It describes the architecture contract, descriptor schema, semantic resource model, graph model, planner behavior, migration phases, and acceptance gates that must be in place before inference smoke tests are allowed.

---

## 1. Problem Statement

The existing Task 11 layer-first work moved a large amount of inference work away from full worker GGUF materialization. It also introduced independent runtime roles such as tokenizer, embedding, pipeline stage, output head, and sampler. However, the current shape still carries legacy assumptions:

- `worker_role::entry` is still used as a substitute for embedding or tokenizer behavior in several paths.
- `layer_start = 0` and `layer_end = 1` can still imply embedding behavior.
- Planner output still contains layer ranges as core identity, not only as one service parameter.
- Install planning can still derive resource placement from legacy entry/final concepts.
- Runtime loading can still translate service roles into legacy worker roles.
- Model-specific knowledge can still leak through ad hoc checks or tensor-name assumptions outside a descriptor boundary.

The result is a fragile system. A small change to planner placement can make install placement wrong. A runtime role can be assigned to one node while its semantic blobs are installed on another. A model family can work only because one branch knows that a particular embedding tensor is called a particular name. These are architecture violations, not just bugs.

Task 11.0 treats this as a design problem before it is treated as an implementation problem. The goal is not to patch every leakage point independently. The goal is to define one semantic source of truth and force every subsystem to use it.

---

## 2. Design Goals

The completed architecture must satisfy the following goals.

1. **Descriptor-owned semantics.** All model-family knowledge belongs to Runtime Descriptor construction and validation. Planner, orchestrator, install planner, node-agent, and runtime consume descriptor facts but do not derive model semantics themselves.

2. **Service-first runtime.** Distributed execution is described in terms of runtime services: tokenizer, embedding, pipeline stage, output head, sampler, and optional modality or adapter services. Services are the planner's unit of placement.

3. **Semantic resources, not blobs.** The install planner places resources such as token embedding, position embedding, output norm, LM head, transformer block weights, RoPE tables, vision encoder, projection, and MoE gate. It does not reason about anonymous blobs or legacy entry/final bundles.

4. **Execution graph before inference.** A validated execution graph must exist before any runtime process is configured. Inference is forbidden if required services, dependencies, or resources are missing.

5. **No architecture branches outside descriptor construction.** There must be no `if llama`, `if qwen`, `if gemma`, `if phi`, `if smollm`, or `if deepseek` in planner, orchestrator, node-agent, install planner, or runtime execution paths.

6. **No legacy role inference.** `worker_role::entry`, `layer_start = 0`, and `layer_end = 1` must not identify embedding, tokenization, or first-stage semantics.

7. **Incremental migration.** The migration must not require rewriting the entire runtime in one step. Each stage introduces narrow, testable invariants and stops after its scope is complete.

---

## 3. Non-Goals

This design does not require immediate removal of all existing compatibility code. Compatibility shims may remain during migration, but they must be fenced behind clearly named compatibility boundaries and must not be part of the target architecture.

This design does not require changing tensor storage format in Task 11.0. The descriptor can describe resources that are currently backed by Layer Store blobs, GGUF-derived tensors, metadata records, or generated tables. The storage backend is not the semantic identity.

This design does not require solving dynamic runtime rebalancing. The initial target is a validated static execution graph per session. Dynamic reassignment can be a later extension if it uses the same descriptor model.

This design does not require implementing multi-modal support immediately. It does require the descriptor model to be able to represent modality services such as vision encoder and projection without changing planner/orchestrator/runtime architecture.

---

## 4. Vocabulary

**Architecture Descriptor** describes the model family and static model structure: architecture name, block count, hidden size, attention shape, tensor naming map, supported modalities, optional heads, and architecture-specific resource extraction rules. It is allowed to know that a model is Llama, Qwen, Gemma, Phi, SmolLM, or DeepSeek.

**Runtime Descriptor** is the architecture-neutral execution contract produced from an Architecture Descriptor and a concrete model manifest. It lists runtime services, semantic resources, service dependencies, resource ownership, cost estimates, validation rules, and graph construction rules. Planner and runtime consume this object.

**Runtime Service** is a semantic unit of inference work. Examples: Tokenizer, Embedding, PipelineStage, OutputHead, Sampler. A service has dependencies, required resources, inputs, outputs, cost, and placement constraints.

**Semantic Resource** is a named model resource with meaning independent of storage layout. Examples: TokenEmbedding, PositionEmbedding, RopeTables, InputNorm, TransformerBlock, OutputNorm, LmHead, SamplingMetadata, VisionEncoder, Projection, MoEGate.

**Execution Graph** is the concrete directed graph of service instances for a model session. It contains nodes such as `Tokenizer`, `Embedding`, `PipelineStage[0]`, `PipelineStage[1]`, `OutputHead`, and `Sampler`, plus typed edges such as token ids, hidden states, logits, and sampled tokens.

**Placement Plan** assigns execution graph service instances to physical nodes. It does not redefine the graph and does not infer service semantics.

**Install Plan** assigns semantic resources to nodes based on the placement plan and Runtime Descriptor. It does not infer resources from layer indices or legacy roles.

---

## 5. What Is Runtime Descriptor?

Runtime Descriptor is the semantic contract between architecture-specific model knowledge and generic distributed runtime systems.

It is not a loader implementation. It is not a planner algorithm. It is not a collection of tensor names. It is not a compatibility wrapper around entry/middle/final worker roles.

Runtime Descriptor answers the question:

> For this model, what services exist, what resources do they need, how do they depend on each other, and what graph is valid to execute?

The descriptor is built before planning. It is validated before placement. It is revalidated before inference. It is included in every decision that could otherwise be tempted to inspect architecture names, layer numbers, or legacy worker roles.

The hierarchy is:

```
Architecture
  -> Architecture Descriptor
      -> Runtime Descriptor
          -> Runtime Service Catalog
          -> Semantic Resource Catalog
          -> Service Dependency Graph
          -> Cost Model Inputs
          -> Execution Graph Template
              -> Concrete Execution Graph
                  -> Placement Plan
                  -> Install Plan
                  -> Runtime Session
```

Architecture Descriptor remains the only architecture-aware layer. It may know tensor naming conventions, optional resource rules, and model-family quirks. Runtime Descriptor removes those quirks from the rest of the system by presenting a normalized service/resource graph.

For example, Qwen and Llama may have different tensor names, tokenizer metadata, RoPE scaling metadata, tied embedding behavior, or output head representation. Planner does not care. Planner sees:

- an Embedding service requiring TokenEmbedding and position/rope resources as declared;
- one or more PipelineStage services requiring TransformerBlock ranges and shared resources;
- an OutputHead service requiring OutputNorm and LmHead, or a tied reference to TokenEmbedding;
- a Sampler service requiring SamplingMetadata.

Runtime Descriptor is therefore the anti-corruption layer between model architecture and distributed runtime.

---

## 6. Runtime Services

Runtime services are semantic inference units. Each service has a stable role name, input contract, output contract, required resources, dependency list, placement constraints, cost estimates, replication support, and validation rules.

### 6.1 Tokenizer Service

Tokenizer converts client text and generation settings into token ids and tokenizer-side metadata.

Inputs:

- raw prompt text;
- optional chat template metadata;
- tokenizer configuration;
- generation request settings that affect tokenization.

Outputs:

- token id sequence;
- token type or segment data if the model requires it;
- prompt length and tokenizer metadata needed by downstream services.

Required semantic resources:

- TokenizerModel;
- TokenizerVocab;
- TokenizerMerges when applicable;
- TokenizerConfig;
- ChatTemplate when applicable;
- SpecialTokenMap;
- TokenizerNormalizer when applicable.

Placement characteristics:

- CPU-oriented;
- low memory compared with model weights;
- supports replication;
- can be colocated with Sampler or client-facing ingress;
- must not imply Embedding or PipelineStage ownership.

Forbidden assumptions:

- Tokenizer is not Entry.
- Tokenizer does not own layer 0.
- Tokenizer does not imply any transformer block.

### 6.2 Embedding Service

Embedding converts token ids into hidden states in model hidden space. It may apply token embedding lookup, position embedding, RoPE preparation, input normalization, or architecture-declared pre-block transforms.

Inputs:

- token ids;
- position ids or sequence positions;
- optional token type data;
- request/session context needed for KV-cache positioning.

Outputs:

- hidden state tensor;
- positional metadata needed by PipelineStage services;
- optional cache position metadata.

Required semantic resources:

- TokenEmbedding;
- PositionEmbedding when explicit learned position embeddings exist;
- RopeTables or RopeParameters when rotary embeddings are prepared here;
- InputNorm when the architecture declares input normalization before the first block;
- EmbeddingScale when the architecture declares scaling;
- TokenTypeEmbedding when applicable;
- EmbeddingProjection when applicable.

Placement characteristics:

- memory-bandwidth sensitive;
- can be colocated with the first PipelineStage but is not identified by it;
- may be replicated for prompt fanout;
- must provide a typed hidden-state output accepted by the first PipelineStage.

Forbidden assumptions:

- Embedding is not Entry.
- Embedding is not `layer_start = 0`.
- Embedding is not `layer_end = 1`.
- Embedding does not require knowing pipeline topology.

### 6.3 PipelineStage Service

PipelineStage executes one or more transformer block service units over hidden states. It is the only base service whose descriptor may carry a block span or block set, and that span is a service parameter, not service identity.

Inputs:

- hidden states from Embedding or previous PipelineStage;
- attention mask or causal mask metadata;
- position and KV-cache metadata;
- runtime cache handles.

Outputs:

- hidden states for the next PipelineStage or OutputHead;
- updated KV-cache state;
- runtime statistics.

Required semantic resources:

- TransformerBlock resources for its assigned block set;
- AttentionNorm per block when applicable;
- AttentionProjection resources;
- AttentionOutputProjection;
- FeedForwardNorm when applicable;
- FeedForwardUpProjection;
- FeedForwardGateProjection when applicable;
- FeedForwardDownProjection;
- MoEGate and ExpertWeights when applicable;
- RopeTables or RopeParameters if consumed inside blocks;
- KVCacheLayout metadata;
- BlockExecutionMetadata.

Placement characteristics:

- usually GPU or accelerator heavy;
- can be split into multiple stages;
- can use pipeline parallelism;
- can support tensor parallelism if the descriptor declares partitionable resources;
- may require strict ordering between block sets.

Forbidden assumptions:

- PipelineStage is not Entry, Middle, or Final.
- A PipelineStage with block set starting at zero is not Embedding.
- The final PipelineStage is not OutputHead.
- Planner must not infer extra semantic resources from first or last block position.

### 6.4 OutputHead Service

OutputHead converts final hidden states into logits. It may apply final normalization, output projection, tied embedding lookup, vocabulary projection, logit scaling, and architecture-declared logits processors that must happen before sampling.

Inputs:

- hidden states from final PipelineStage or directly from Embedding for degenerate models;
- position/request metadata;
- optional adapter metadata.

Outputs:

- logits;
- optional logit metadata for sampler;
- output hidden states if requested.

Required semantic resources:

- OutputNorm;
- LmHead or tied TokenEmbedding reference;
- OutputProjection when distinct from LmHead;
- LogitScale or output scaling metadata;
- VocabularyMetadata;
- AdapterOutputProjection when active.

Placement characteristics:

- often memory heavy due to vocabulary projection;
- may be colocated with final PipelineStage but is not identified by it;
- can support replication for batched decode if resources fit;
- requires exact agreement with tokenizer vocabulary metadata.

Forbidden assumptions:

- OutputHead is not Final.
- OutputHead is not `layer_end = n_layer`.
- OutputHead does not imply Sampler.

### 6.5 Sampler Service

Sampler converts logits into next token ids according to generation policy.

Inputs:

- logits;
- sampling parameters;
- random seed/state;
- vocabulary and special-token constraints;
- optional grammar or structured-output constraints.

Outputs:

- sampled token ids;
- sampling trace if requested;
- stopping metadata.

Required semantic resources:

- SamplingMetadata;
- VocabularyMetadata;
- SpecialTokenMap;
- GenerationPolicyDefaults;
- GrammarMetadata when requested;
- LogitsProcessorConfig.

Placement characteristics:

- CPU-friendly for small batch sizes;
- can be replicated;
- may be colocated with Tokenizer;
- does not need model block weights.

Forbidden assumptions:

- Sampler does not own OutputHead.
- Sampler does not know model architecture.
- Sampler does not inspect tensor names.

### 6.6 Optional VisionEncoder Service

VisionEncoder converts images or visual embeddings into model-compatible hidden states or intermediate embeddings.

Required semantic resources:

- VisionPatchEmbedding;
- VisionPositionEmbedding;
- VisionTransformerBlocks;
- VisionNorm;
- VisionProjection;
- ImagePreprocessMetadata.

The base text-only runtime graph does not require this service, but the descriptor schema must support it without changing planner or orchestrator architecture.

### 6.7 Optional Projection Service

Projection maps modality outputs, adapter outputs, or embedding variants into the main model hidden space.

Required semantic resources:

- ModalityProjection;
- AdapterProjection;
- EmbeddingProjection;
- ProjectionNorm when applicable;
- ProjectionMetadata.

Projection can appear between VisionEncoder and PipelineStage, between Embedding variants and PipelineStage, or before OutputHead depending on descriptor graph rules.

### 6.8 Optional Adapter Service

Adapter applies LoRA, prompt adapter, routing adapter, or other extension resources.

Required semantic resources:

- AdapterWeights;
- AdapterConfig;
- AdapterRoutingMetadata;
- AdapterMergePolicy.

Adapter can be a standalone service or a resource overlay on PipelineStage/Embedding/OutputHead depending on descriptor policy.

---

## 7. Semantic Resources

Semantic resources are not blobs. A blob is a storage unit. A semantic resource is a meaningful runtime dependency. Multiple semantic resources may be stored in one blob, and one semantic resource may be sharded across multiple blobs.

The descriptor must name resources by meaning. Install planner places resources by meaning. Runtime validates resources by meaning. Storage resolution maps semantic resources to concrete Layer Store records.

### 7.1 Core Metadata Resources

- ModelMetadata: model dimensions, block count, vocabulary size, dtype, quantization metadata.
- ArchitectureMetadata: normalized architecture identity and descriptor version.
- TensorNameMap: mapping from semantic resources to storage tensor names.
- QuantizationMetadata: quantization type, block size, scale layout, dequant policy.
- ChecksumManifest: integrity data for resources and backing blobs.
- RuntimeCompatibilityMetadata: minimum runtime capabilities required to execute the descriptor.

### 7.2 Tokenizer Resources

- TokenizerModel;
- TokenizerVocab;
- TokenizerMerges;
- TokenizerConfig;
- TokenizerNormalizer;
- TokenizerPreTokenizer;
- TokenizerPostProcessor;
- ChatTemplate;
- SpecialTokenMap;
- AddedTokens;
- VocabularyMetadata.

### 7.3 Embedding Resources

- TokenEmbedding;
- PositionEmbedding;
- TokenTypeEmbedding;
- RopeTables;
- RopeParameters;
- InputNorm;
- EmbeddingScale;
- EmbeddingProjection;
- EmbeddingBias;
- PromptPrefixEmbedding;
- MultimodalInputEmbedding.

### 7.4 Pipeline Resources

- TransformerBlock;
- AttentionNorm;
- AttentionQueryProjection;
- AttentionKeyProjection;
- AttentionValueProjection;
- AttentionOutputProjection;
- AttentionQKNorm;
- AttentionBias;
- RopeParameters;
- RopeTables;
- AlibiSlopes;
- FeedForwardNorm;
- FeedForwardUpProjection;
- FeedForwardGateProjection;
- FeedForwardDownProjection;
- FeedForwardActivationMetadata;
- ParallelResidualMetadata;
- KVCacheLayout;
- BlockExecutionMetadata.

### 7.5 Output Resources

- OutputNorm;
- LmHead;
- TiedLmHeadReference;
- OutputProjection;
- OutputBias;
- LogitScale;
- FinalLogitsProcessorMetadata;
- VocabularyMetadata.

### 7.6 Sampler Resources

- SamplingMetadata;
- GenerationPolicyDefaults;
- SpecialTokenMap;
- GrammarMetadata;
- LogitsProcessorConfig;
- StopSequenceMetadata;
- RandomPolicyMetadata.

### 7.7 Mixture-of-Experts Resources

- MoEGate;
- ExpertWeights;
- ExpertUpProjection;
- ExpertGateProjection;
- ExpertDownProjection;
- ExpertRoutingMetadata;
- ExpertParallelismMetadata;
- SharedExpertWeights;
- ExpertLoadBalancingMetadata.

### 7.8 Multi-Modal Resources

- VisionEncoder;
- VisionPatchEmbedding;
- VisionPositionEmbedding;
- VisionTransformerBlock;
- VisionAttentionResources;
- VisionFeedForwardResources;
- VisionNorm;
- VisionProjection;
- ImagePreprocessMetadata;
- AudioEncoder;
- AudioProjection;
- ModalityProjection;
- ModalityRouterMetadata.

### 7.9 Adapter and Fine-Tuning Resources

- AdapterWeights;
- AdapterConfig;
- AdapterProjection;
- AdapterRoutingMetadata;
- LoRAA;
- LoRAB;
- LoRAScale;
- PromptAdapterEmbedding;
- AdapterMergePolicy.

### 7.10 Runtime-Generated Resources

- KVCacheAllocation;
- KVCacheLayout;
- RopeRuntimeCache;
- AttentionMaskCache;
- TokenBuffer;
- HiddenStateBuffer;
- LogitsBuffer;
- RuntimeScratchBuffer.

Runtime-generated resources are not installed from Layer Store, but the descriptor can declare that a service requires them so memory planning and validation are complete.

---

## 8. Data Needed by Each Role

This section intentionally describes semantic resources, not layers.

### 8.1 Tokenizer

Tokenizer needs tokenization resources and generation metadata:

- TokenizerModel or tokenizer algorithm metadata;
- vocabulary and merge rules;
- special-token map;
- normalizer/pre-tokenizer/post-processor rules;
- chat template when request format uses it;
- added token definitions;
- generation stop token metadata.

Tokenizer does not need token embedding, transformer blocks, output norm, LM head, or layer ranges.

### 8.2 Embedding

Embedding needs resources that create the first hidden state:

- TokenEmbedding;
- PositionEmbedding or RopeParameters/RopeTables, depending on architecture contract;
- TokenTypeEmbedding when present;
- InputNorm when declared before block execution;
- EmbeddingScale or embedding projection when declared;
- hidden-state dtype and layout metadata.

Embedding does not need Entry, Layer0 identity, pipeline topology, output norm, LM head, sampler policy, or final block resources.

### 8.3 PipelineStage

PipelineStage needs only block execution resources for its assigned block set and shared runtime metadata:

- TransformerBlock resources for assigned block set;
- per-block attention resources;
- per-block feed-forward resources;
- per-block norms;
- MoE routing and expert resources if the block set uses MoE;
- RoPE/ALiBi parameters if consumed inside attention;
- KV-cache layout metadata;
- hidden-state layout metadata.

PipelineStage does not need tokenizer state, token embedding unless the descriptor explicitly declares tied in-block reuse, output head unless the descriptor explicitly declares an output service colocated by placement, or sampler metadata.

### 8.4 OutputHead

OutputHead needs resources that map final hidden state to logits:

- OutputNorm;
- LmHead or TiedLmHeadReference;
- OutputProjection when separate;
- output bias if present;
- logit scale;
- vocabulary metadata;
- final logits processor metadata if it is architecture-owned rather than request-owned.

OutputHead does not need Entry, final worker role, sampler RNG state, tokenizer implementation, or pipeline layer boundaries.

### 8.5 Sampler

Sampler needs resources and request metadata for token selection:

- SamplingMetadata;
- generation defaults;
- vocabulary metadata;
- special-token and stop-token metadata;
- request-level sampling parameters;
- grammar or constrained decoding metadata when requested.

Sampler does not need model weights, transformer blocks, embedding weights, output norm, or architecture-specific tensor names.

### 8.6 Install Planner

Install Planner needs:

- Runtime Descriptor resource catalog;
- concrete placement plan;
- resource-to-service requirements;
- resource replication/sharding policy;
- resource storage mapping;
- resource checksums and size estimates.

Install Planner does not need layer_start, layer_end, Entry, Final, or architecture names.

### 8.7 Orchestrator

Orchestrator needs:

- validated Runtime Descriptor id/version;
- concrete Execution Graph;
- placement plan;
- install readiness report;
- service endpoint contracts;
- session lifecycle policy.

Orchestrator does not need to know which tensor names form Llama embedding, how Qwen names output norm, whether Gemma has special norm placement, or which layer index corresponds to embedding.

### 8.8 Node Agent

Node Agent needs:

- service assignment for this node;
- semantic resource handles already assigned to that service;
- runtime binding parameters;
- local Layer Store availability;
- service input/output endpoint definitions.

Node Agent does not substitute roles. It does not turn Embedding into Entry. It does not infer embedding from layer range. It does not branch by architecture.

---

## 9. Required Dependencies

Runtime service dependencies form a directed graph. The default text generation graph is:

```
Tokenizer
  -> Embedding
      -> PipelineStage[0]
          -> PipelineStage[1]
              -> ...
                  -> PipelineStage[N]
                      -> OutputHead
                          -> Sampler
```

Typed data edges:

- Tokenizer to Embedding: token ids, token metadata, prompt positions.
- Embedding to PipelineStage: hidden states, position metadata, cache position metadata.
- PipelineStage to PipelineStage: hidden states, KV-cache handles, runtime sequence metadata.
- PipelineStage to OutputHead: final hidden states.
- OutputHead to Sampler: logits and vocabulary metadata reference.
- Sampler to Tokenizer/client loop: sampled token ids and stopping metadata.

Resource dependencies:

- Tokenizer depends on tokenizer resources.
- Embedding depends on embedding resources and hidden layout metadata.
- PipelineStage depends on block resources and cache layout metadata.
- OutputHead depends on output projection resources and vocabulary metadata.
- Sampler depends on sampling metadata and vocabulary metadata.

Validation dependencies:

- Every service dependency must point to an existing service.
- Every required semantic resource must exist in the resource catalog.
- Every installed resource must satisfy checksum and compatibility constraints.
- Every graph edge must connect output type to accepted input type.
- Every service placement must satisfy memory, compute, network, and capability requirements.

---

## 10. Dependencies That Must Not Exist

The following dependencies are forbidden in the target architecture.

Embedding must not depend on:

- Entry;
- Layer0 identity;
- `layer_start = 0`;
- `layer_end = 1`;
- first PipelineStage placement;
- pipeline topology;
- architecture name.

Tokenizer must not depend on:

- Entry;
- Embedding;
- any transformer block;
- output head;
- architecture-specific runtime branches.

PipelineStage must not depend on:

- Entry/Middle/Final;
- tokenizer ownership;
- embedding ownership;
- output head ownership;
- model family names;
- special first/last layer semantics outside descriptor service parameters.

OutputHead must not depend on:

- Final;
- last PipelineStage identity;
- `layer_end = n_layer`;
- sampler ownership;
- architecture-specific branches outside descriptor construction.

Sampler must not depend on:

- OutputHead placement;
- model weights;
- transformer layers;
- architecture name.

Planner must not depend on:

- `worker_role::entry`;
- `worker_role::middle`;
- `worker_role::final`;
- `layer_start` or `layer_end` as semantic indicators;
- architecture names;
- tensor names.

Install Planner must not depend on:

- entry/final placement;
- layer ranges for non-pipeline resources;
- blob ids that hide semantic meaning;
- architecture names.

Orchestrator must not depend on:

- resource selection logic;
- tensor naming logic;
- architecture names;
- implicit role substitution.

Node Agent must not depend on:

- role substitution;
- embedding through Entry;
- tokenizer through Entry;
- output through Final;
- architecture names.

Runtime must not depend on:

- `if llama`;
- `if qwen`;
- `if gemma`;
- `if phi`;
- `if smollm`;
- `if deepseek`;
- legacy role inference.

---

## 11. Runtime Descriptor Schema

This is a schema description, not implementation code. Field names are normative for design discussion, but exact C++ naming can be decided during implementation as long as the contract is preserved.

### 11.1 Descriptor Identity

Runtime Descriptor contains:

- descriptor_id: stable id derived from model id, architecture descriptor id, and descriptor schema version;
- schema_version: version of the Runtime Descriptor schema;
- model_id: concrete model identity;
- architecture_descriptor_id: source Architecture Descriptor id;
- created_from_manifest_id: manifest/checksum identity;
- compatibility_version: minimum runtime compatibility version;
- validation_status: result of descriptor validation.

### 11.2 Architecture Descriptor to Runtime Descriptor

Architecture Descriptor contributes:

- architecture family metadata;
- tensor naming map;
- model dimensions;
- block inventory;
- optional feature inventory;
- tokenizer metadata;
- output head rules;
- tied embedding rules;
- MoE structure;
- modality structure;
- resource extraction rules.

Runtime Descriptor normalizes this into:

- service descriptors;
- semantic resource descriptors;
- dependency descriptors;
- graph template;
- cost descriptors;
- validation constraints.

After conversion, generic systems must only read Runtime Descriptor.

### 11.3 Runtime Service Descriptor

Each service descriptor contains:

- name: semantic service name, such as Tokenizer, Embedding, PipelineStage, OutputHead, Sampler;
- service_kind: normalized kind from the service catalog;
- service_id: stable id within descriptor;
- dependencies: upstream services required before this service can execute;
- required_resources: semantic resources required locally or remotely;
- optional_resources: resources that enable optional behavior;
- input_contract: typed inputs accepted by the service;
- output_contract: typed outputs produced by the service;
- memory_cost: static and runtime memory estimate;
- compute_cost: expected compute weight and preferred device class;
- network_cost: expected input/output transfer size and latency sensitivity;
- cache_cost: KV-cache or scratch-buffer requirements;
- supports_parallel: whether service can be split across nodes;
- supports_replication: whether service can be replicated;
- supports_colocation: service kinds that may be colocated without changing semantics;
- placement_constraints: required node capabilities, memory limits, device types;
- lifecycle: configure/start/execute/stop requirements;
- validation_rules: service-specific descriptor invariants.

### 11.4 Semantic Resource Descriptor

Each semantic resource descriptor contains:

- resource_id: stable semantic id;
- resource_kind: normalized semantic resource kind;
- producer: descriptor component that declares the resource;
- consumers: services that require the resource;
- storage_mapping: Layer Store or manifest mapping;
- required: whether inference is invalid without it;
- shareable: whether multiple services can reference one installed copy;
- shardable: whether the resource can be split;
- replicated: whether the resource should be installed on each consuming node;
- tied_to: another resource if this is an alias/reference;
- size_bytes: estimated stored size;
- runtime_size_bytes: estimated loaded size;
- dtype: data type;
- quantization: quantization metadata;
- checksum: integrity metadata;
- layout: tensor/resource layout contract;
- architecture_origin: source Architecture Descriptor reference for debugging only.

The `architecture_origin` field is diagnostic. Generic systems must not branch on it.

### 11.5 Dependency Descriptor

Each dependency descriptor contains:

- from_service;
- to_service;
- edge_kind;
- data_contract;
- ordering_required;
- streaming_supported;
- backpressure_policy;
- failure_policy;
- retry_policy.

Edge kinds include:

- token_ids;
- hidden_states;
- logits;
- sampled_tokens;
- cache_handles;
- control_metadata.

### 11.6 Cost Descriptor

Cost descriptors are estimates used by planner. They are not architecture branches.

Each cost descriptor contains:

- memory_static_bytes;
- memory_runtime_bytes_per_token;
- memory_runtime_bytes_per_sequence;
- compute_weight_prefill;
- compute_weight_decode;
- preferred_device_class;
- network_bytes_per_token;
- network_bytes_per_prompt_token;
- latency_sensitivity;
- replication_cost;
- colocation_bonus;
- split_penalty.

Planner may use these values but must not override them based on architecture names.

### 11.7 Execution Graph Template

The graph template contains:

- required service kinds;
- optional service kinds;
- legal service ordering;
- legal service multiplicity;
- legal PipelineStage partition policy;
- graph validation rules;
- typed edge definitions.

For text-only decoder models, required service kinds are normally Tokenizer, Embedding, at least one PipelineStage, OutputHead, and Sampler.

### 11.8 Placement Contract

Placement is a derived plan, not part of descriptor identity. It contains:

- graph service instance id;
- assigned node id;
- assigned endpoint;
- service parameters;
- resource locality requirements;
- replication group when applicable;
- colocation group when applicable.

PipelineStage service parameters may include block set or block range. These parameters are not used to infer other service semantics.

### 11.9 Validation Contract

`validate_runtime_descriptor` must check:

- descriptor schema version is supported;
- required service kinds exist;
- service graph is connected and acyclic for prefill/decode execution;
- Tokenizer, Embedding, Pipeline, OutputHead, and Sampler exist for decoder-only text generation;
- all service dependencies exist;
- all required semantic resources exist;
- every required resource has a valid storage mapping or runtime generation rule;
- tied resources point to valid targets;
- no service consumes a resource that is undeclared;
- graph edge input/output contracts match;
- cost fields are present for planner-required services;
- every architecture-required optional feature is represented as service/resource descriptors;
- no legacy Entry/Middle/Final role is required by descriptor.

If validation fails, inference must not start.

### 11.10 Runtime Descriptor Builder

Runtime Descriptor must be produced by one explicit component: Runtime Descriptor Builder, also called Descriptor Compiler.

The required pipeline is:

```
GGUF / model source
  -> Model Manifest
  -> Architecture Descriptor
  -> Runtime Descriptor Builder
  -> Runtime Descriptor
  -> Runtime Descriptor Validator
  -> Execution Graph Builder
  -> Execution Graph Validator
```

Runtime Descriptor Builder is the only component allowed to transform architecture-specific facts into runtime semantics. It compiles Architecture Descriptor data into service descriptors, semantic resources, graph templates, costs, capabilities, and validation rules.

Runtime Descriptor Builder owns:

- converting tensor naming maps into semantic resources;
- deciding which Runtime Services exist for a model;
- declaring service dependencies;
- declaring input/output contracts;
- declaring resource consumers;
- declaring tied resources;
- declaring graph templates;
- declaring execution capabilities;
- declaring default failure policies;
- declaring descriptor validation rules.

Runtime Descriptor Builder must not:

- perform node placement;
- inspect cluster inventory;
- decide install locations;
- configure workers;
- start runtime processes;
- execute inference;
- silently patch invalid architecture descriptors.

Every architecture-specific plugin feeds Architecture Descriptor data into the builder. Architecture-specific code must stop there. Planner, install planner, orchestrator, node-agent, runtime scheduler, and runtime execution read the produced Runtime Descriptor, not architecture plugin internals.

This boundary prevents descriptor construction logic from being spread across planner files, install planner files, orchestrator setup code, and runtime loader branches. If a new model family needs special handling, that handling must appear before or inside Runtime Descriptor Builder, and the result must be visible as normalized Runtime Descriptor facts.

Builder output must be deterministic. The same model manifest and Architecture Descriptor version must produce the same Runtime Descriptor id, service catalog, resource catalog, graph template, and capability set.

---

## 12. Role Planner Design

The Role Planner consumes:

- Runtime Descriptor;
- cluster node inventory;
- resource availability and install state;
- performance/capability telemetry;
- request/session constraints.

The Role Planner produces:

- concrete Execution Graph service instances;
- placement plan mapping service instances to nodes;
- PipelineStage partition parameters;
- replication and colocation choices;
- install requirements derived from placement.

The Role Planner does not consume:

- architecture names;
- tensor names;
- Entry/Middle/Final;
- layer ranges as semantic role indicators.

### 12.1 Planner Input Model

Planner sees service descriptors, not hand-coded model structure. It receives entries such as:

- Tokenizer requires tokenizer resources, CPU capability, supports replication;
- Embedding requires TokenEmbedding and position resources, memory bandwidth sensitive;
- PipelineStage template requires TransformerBlock partitioning, accelerator preferred;
- OutputHead requires OutputNorm and LmHead, high memory bandwidth;
- Sampler requires sampling metadata, CPU capability, supports replication.

Planner may split PipelineStage according to descriptor partition policy. It may choose one or many pipeline service instances. But it cannot create Embedding by choosing block zero or by assigning Entry.

### 12.2 Example Planner Output

Example service placement:

```
Tokenizer      -> Node C
Embedding      -> Node C
PipelineStage  -> Node A, block set 0..7
PipelineStage  -> Node D, block set 8..15
PipelineStage  -> Node A, block set 16..23
OutputHead     -> Node B
Sampler        -> Node C
```

The block ranges are parameters of PipelineStage instances only. They do not define Tokenizer, Embedding, or OutputHead.

### 12.3 Planner Cost Behavior

Planner uses descriptor-provided costs:

- Tokenizer: CPU score, low memory, low network output;
- Embedding: memory bandwidth, hidden-state output network cost;
- PipelineStage: compute weight, block resource memory, KV-cache cost;
- OutputHead: vocabulary projection memory and compute;
- Sampler: CPU and request policy complexity.

Planner can colocate services when cost model supports it. For example, Tokenizer and Sampler may colocate. Embedding and first PipelineStage may colocate. OutputHead and final PipelineStage may colocate. Colocation is a placement optimization, never semantic identity.

### 12.4 Planner Equivalence During Migration

During migration, Planner must support equivalence tests:

- legacy planner result converted into descriptor graph;
- new planner result generated from descriptor;
- comparison of service placement and resource requirements;
- identical behavior for supported legacy scenarios where the old behavior was valid;
- explicit differences only when old behavior violated invariants.

Equivalence does not mean retaining legacy Entry semantics. It means preserving user-visible behavior and resource readiness while removing invalid role inference.

---

## 13. Install Planner Design

Install Planner consumes:

- Runtime Descriptor semantic resource catalog;
- concrete placement plan;
- resource storage mappings;
- node inventory and resource availability.

Install Planner produces:

- per-node semantic resource install operations;
- per-node validation requirements;
- resource replication plan;
- resource sharing plan.

Install Planner must answer:

> Which semantic resources must be present on each node for its assigned services to execute?

It must not answer:

> Which blobs does Entry usually need?

Example:

```
Node C:
  Tokenizer service -> TokenizerModel, TokenizerVocab, SpecialTokenMap
  Embedding service -> TokenEmbedding, RopeParameters, InputNorm
  Sampler service   -> SamplingMetadata, VocabularyMetadata, SpecialTokenMap

Node A:
  PipelineStage[0]  -> TransformerBlock[0..7], KVCacheLayout, RopeParameters
  PipelineStage[2]  -> TransformerBlock[16..23], KVCacheLayout, RopeParameters

Node B:
  OutputHead        -> OutputNorm, LmHead, VocabularyMetadata
```

Install readiness must be evaluated against semantic resources, not only layer coverage. A node can have all transformer blocks and still be invalid if its Embedding service lacks TokenEmbedding. A cluster can have complete layer coverage and still be invalid if OutputHead resources are installed on the wrong node.

---

## 14. Execution Graphs by Architecture

These graphs are architecture-neutral at the service level. Differences are expressed in descriptor resources and graph annotations, not planner or runtime branches.

### 14.1 Llama

```
Tokenizer
  -> Embedding
       resources: TokenEmbedding, RopeParameters
  -> PipelineStage[0..k]
       resources: TransformerBlock, Attention projections, FFN projections, RMS norms, RopeParameters
  -> OutputHead
       resources: OutputNorm, LmHead or tied TokenEmbedding
  -> Sampler
       resources: SamplingMetadata, VocabularyMetadata
```

Descriptor notes:

- RoPE is represented as RopeParameters or generated RopeTables.
- Output head may be tied or separate depending on concrete model metadata.
- RMS norm is represented as InputNorm, per-block norms, and OutputNorm where applicable.

### 14.2 Qwen

```
Tokenizer
  -> Embedding
       resources: TokenEmbedding, RopeParameters, tokenizer-specific metadata
  -> PipelineStage[0..k]
       resources: TransformerBlock, Attention Q/K/V/O, MLP gate/up/down, norms, RopeParameters
  -> OutputHead
       resources: OutputNorm, LmHead or tied TokenEmbedding, VocabularyMetadata
  -> Sampler
```

Descriptor notes:

- Qwen tokenizer and special-token behavior live in Tokenizer resources.
- Any Qwen-specific tensor naming is resolved inside Architecture Descriptor to semantic resources.
- Planner sees the same service graph as Llama.

### 14.3 Gemma

```
Tokenizer
  -> Embedding
       resources: TokenEmbedding, EmbeddingScale if declared, RopeParameters
  -> PipelineStage[0..k]
       resources: TransformerBlock, attention resources, gated FFN resources, norm resources
  -> OutputHead
       resources: OutputNorm, LmHead or tied TokenEmbedding, LogitScale if declared
  -> Sampler
```

Descriptor notes:

- Gemma-specific normalization, scaling, or tied output behavior is descriptor data.
- Runtime does not branch on Gemma.
- Output scaling is an OutputHead resource or service parameter.

### 14.4 Phi

```
Tokenizer
  -> Embedding
       resources: TokenEmbedding, Position/Rope resources as descriptor declares
  -> PipelineStage[0..k]
       resources: TransformerBlock, attention resources, MLP resources, norm/residual metadata
  -> OutputHead
       resources: OutputNorm, LmHead, VocabularyMetadata
  -> Sampler
```

Descriptor notes:

- Any parallel residual or block-layout difference is BlockExecutionMetadata.
- Planner sees cost and resource shape, not Phi-specific code paths.

### 14.5 SmolLM

```
Tokenizer
  -> Embedding
       resources: TokenEmbedding, RopeParameters
  -> PipelineStage[0..k]
       resources: TransformerBlock, attention resources, FFN resources, norms
  -> OutputHead
       resources: OutputNorm, LmHead or tied TokenEmbedding
  -> Sampler
```

Descriptor notes:

- SmolLM remains a standard decoder graph unless the descriptor declares a concrete optional service.
- Small-model placement may colocate all services, but colocation does not change graph semantics.

### 14.6 DeepSeek

```
Tokenizer
  -> Embedding
       resources: TokenEmbedding, RopeParameters
  -> PipelineStage[0..k]
       resources: TransformerBlock, attention resources, MoEGate, ExpertWeights, routing metadata when present
  -> OutputHead
       resources: OutputNorm, LmHead or tied TokenEmbedding
  -> Sampler
```

Descriptor notes:

- DeepSeek dense variants and distilled variants are expressed by resource inventory.
- MoE variants include MoEGate, ExpertWeights, ExpertRoutingMetadata, and ExpertParallelismMetadata.
- Planner may use higher memory and network costs from descriptor, but does not branch on DeepSeek.

---

## 15. Runtime Data Plane

The logical graph is not enough. The runtime must also define how data physically moves through the cluster for prefill and decode. The data plane is the concrete transport path between placed service instances.

The canonical physical path is:

```
Client
  -> Tokenizer node
       sends: prompt text, request metadata
       returns/forwards: token ids
  -> Embedding node
       receives: token ids, position metadata
       sends: hidden states
  -> PipelineStage node 1
       receives: hidden states
       sends: hidden states
  -> PipelineStage node 2
       receives: hidden states
       sends: hidden states
  -> ...
  -> OutputHead node
       receives: final hidden states
       sends: logits
  -> Sampler node
       receives: logits, sampling policy
       sends: sampled token ids
  -> Client
```

This path is derived from the concrete Execution Graph and placement plan. It is not hard-coded by architecture or legacy roles.

### 15.1 Data Plane Messages

Every edge in the Execution Graph has a typed physical message:

- Client to Tokenizer: prompt text, request id, session id, generation options.
- Tokenizer to Embedding: token ids, prompt length, token metadata, position base.
- Embedding to PipelineStage: hidden state tensor, dtype, shape, hidden size, sequence range, position metadata.
- PipelineStage to PipelineStage: hidden state tensor, KV-cache references, sequence range, block execution metadata.
- PipelineStage to OutputHead: final hidden state tensor, dtype, shape, vocabulary context reference.
- OutputHead to Sampler: logits tensor, vocab size, logit dtype, logit metadata.
- Sampler to Client: sampled token id, stop state, optional sampling trace.

Each message must carry enough metadata for the receiver to validate shape, dtype, sequence position, and session ownership before execution.

### 15.2 Prefill Data Plane

Prefill processes the full prompt sequence:

```
Client prompt
  -> Tokenizer: full prompt tokens
  -> Embedding: hidden[prompt_tokens]
  -> PipelineStage chain: hidden[prompt_tokens], KV writes
  -> OutputHead: logits[last_position]
  -> Sampler: first generated token
```

Prefill is bandwidth sensitive because hidden states can be large. Runtime Scheduler must respect backpressure from downstream PipelineStage services and must not enqueue decode until prefill cache state is committed.

### 15.3 Decode Data Plane

Decode processes one or more generated tokens:

```
Sampler token
  -> Embedding: hidden[next_token]
  -> PipelineStage chain: hidden[next_token], KV reads/writes
  -> OutputHead: logits[next_position]
  -> Sampler: next token
```

Decode is latency sensitive. Runtime Scheduler may keep a decode loop resident across services, but it still uses the same graph edges and message contracts. No service is allowed to bypass the graph because it is colocated with another service.

### 15.4 Colocation and Data Plane Shortcuts

If Tokenizer and Embedding are on the same node, token ids may be passed through an in-process queue. If Embedding and first PipelineStage are colocated, hidden states may stay in local memory. If OutputHead and Sampler are colocated, logits may avoid network transfer.

These are transport optimizations only. They do not change service identity, resource ownership, graph validation, or failure semantics.

### 15.5 Data Plane Observability

Every session should expose per-edge metrics:

- message count;
- bytes sent;
- serialization time;
- network latency;
- queue wait time;
- execution time after receive;
- backpressure events;
- retry count;
- last successful sequence position.

These metrics are necessary to debug whether a failure is descriptor invalidity, install invalidity, scheduler stall, node failure, or pure data-plane bottleneck.

---

## 16. Session Lifecycle

Session lifecycle is the operational path from model discovery to teardown. It must be explicit so the system does not mix descriptor validation, placement, install, worker configure, and inference.

The required lifecycle is:

```
Discover model
  -> Build/Load Manifest
  -> Build Architecture Descriptor
  -> Build Runtime Descriptor
  -> Validate Runtime Descriptor
  -> Build Execution Graph
  -> Validate Execution Graph
  -> Plan Placement
  -> Plan Install
  -> Validate Install
  -> Configure Workers
  -> Wait Worker Ready
  -> Create Session
  -> Run Prefill
  -> Run Decode
  -> Finish or Cancel
  -> Destroy Session
  -> Release Runtime Resources
```

### 16.1 Session Create

Session Create must do only orchestration work:

- identify model and descriptor versions;
- require successful Runtime Descriptor validation;
- require successful Execution Graph validation;
- require a placement plan;
- require install readiness for all placed services;
- configure service instances on node-agents;
- wait for service readiness;
- publish a session id only after all required services are ready.

Session Create must not:

- select architecture-specific resources;
- infer Embedding from Entry;
- infer OutputHead from Final;
- launch inference before readiness;
- silently fall back to materialized GGUF if descriptor runtime validation failed.

### 16.2 Worker Configure

Worker Configure receives:

- session id;
- service instance id;
- service kind;
- assigned semantic resources;
- input edge definitions;
- output edge definitions;
- runtime capability flags;
- cache allocation contract;
- scheduler endpoint.

Worker Configure returns:

- accepted/rejected status;
- local resource validation status;
- endpoint readiness;
- runtime memory reservation;
- supported execution operations;
- failure policy supported by the worker.

### 16.3 Worker Ready

A worker is Ready only when:

- all required semantic resources are locally available or valid remote references;
- resource checksums match;
- service input/output contracts are registered;
- cache and scratch buffers are allocated;
- scheduler heartbeat is active;
- service state machine is in READY;
- no compatibility fallback was used to satisfy a descriptor-owned requirement.

### 16.4 Inference

Inference starts only after Session Ready. Runtime Scheduler drives prefill and decode over graph edges. Orchestrator may observe, cancel, or destroy the session, but it must not perform per-token architecture logic.

### 16.5 Destroy

Destroy releases:

- scheduler queues;
- service runtime state;
- KV-cache allocations;
- temporary hidden/logits buffers;
- per-session resource pins;
- service endpoints;
- heartbeat registrations.

Destroy must be idempotent. A failed session must still be destroyable even when one or more nodes are unavailable.

---

## 17. Runtime State Machines

State machines make races visible. They are separate for model lifecycle, session lifecycle, and service lifecycle.

### 17.1 Model/Descriptor State

```
DISCOVERED
  -> MANIFEST_READY
  -> ARCHITECTURE_DESCRIPTOR_READY
  -> RUNTIME_DESCRIPTOR_READY
  -> DESCRIPTOR_VALID
  -> GRAPH_READY
  -> GRAPH_VALID
  -> PLACEMENT_READY
  -> INSTALL_READY
  -> RUNTIME_READY
```

Invalid transitions:

- MANIFEST_READY directly to PLACEMENT_READY;
- RUNTIME_DESCRIPTOR_READY directly to INSTALL_READY without descriptor validation;
- GRAPH_READY directly to RUNTIME_READY without graph validation;
- INSTALL_READY before placement exists.

### 17.2 Session State

```
CREATED
  -> VALIDATING
  -> PLACING
  -> INSTALL_CHECKING
  -> CONFIGURING
  -> WAITING_WORKERS
  -> SESSION_READY
  -> RUNNING_PREFILL
  -> RUNNING_DECODE
  -> FINISHED
  -> DESTROYING
  -> DESTROYED
```

Failure states:

```
FAILED_VALIDATION
FAILED_PLACEMENT
FAILED_INSTALL
FAILED_CONFIGURE
FAILED_RUNTIME
CANCELLED
```

Rules:

- RUNNING_PREFILL requires SESSION_READY.
- RUNNING_DECODE requires completed prefill for that sequence.
- DESTROYING is valid from any non-destroyed state.
- FINISHED requires sampler stop state or max-token completion.

### 17.3 Service State

Each runtime service instance uses:

```
CREATED
  -> CONFIGURED
  -> RESOURCES_BOUND
  -> READY
  -> RUNNING
  -> DRAINING
  -> STOPPED
```

Failure states:

```
RESOURCE_MISSING
CONFIG_REJECTED
HEALTH_LOST
EXECUTION_FAILED
RESTARTING
RELOCATING
```

Rules:

- READY requires RESOURCES_BOUND.
- RUNNING requires scheduler assignment.
- STOPPED must release per-session runtime resources.
- RESTARTING and RELOCATING are allowed only if Execution Capabilities permit them.

### 17.4 Edge State

Each data-plane edge uses:

```
DECLARED
  -> CONNECTED
  -> FLOWING
  -> BACKPRESSURED
  -> DRAINING
  -> CLOSED
```

Failure states:

```
TYPE_MISMATCH
SHAPE_MISMATCH
TIMEOUT
UPSTREAM_LOST
DOWNSTREAM_LOST
```

Edge state is what lets the system distinguish "PipelineStage2 died" from "Embedding produced hidden shape the next stage cannot accept."

---

## 18. Failure Model

The target runtime must assume failures happen. The descriptor and execution graph define what can be recovered. The Runtime Scheduler applies the recovery policy. Orchestrator observes and enforces session-level outcomes.

### 18.1 Failure Classification

Failures are classified by scope:

- descriptor failure: invalid services/resources/dependencies;
- placement failure: no node can satisfy service constraints;
- install failure: required semantic resources missing or invalid;
- configure failure: worker rejects service assignment;
- readiness failure: service configured but not ready;
- data-plane failure: edge timeout, type mismatch, shape mismatch, transport loss;
- execution failure: service crashes or returns invalid output;
- node failure: heartbeat lost or node unreachable;
- scheduler failure: queue deadlock, backpressure timeout, invalid transition.

Failures are classified by recovery possibility:

- fatal: session must be destroyed;
- retryable: same service can retry operation;
- restartable: service can restart on same node;
- relocatable: service can move to another node;
- resumable: session can continue from cache/checkpoint;
- degraded: session can continue with lower performance or reduced replication.

### 18.2 Embedding Node Failure

If Embedding node dies before prefill:

- if `can_restart`, restart the service on the same node after resources are verified;
- else if `can_migrate` and resources are installed elsewhere or install can complete, relocate Embedding and reconnect edges;
- else destroy the session.

If Embedding node dies during prefill:

- current prefill operation is invalid;
- downstream PipelineStage services must drain or discard partial hidden state for that request;
- session may restart prefill only if request state and cache state are clean;
- otherwise destroy the session.

If Embedding node dies during decode:

- scheduler pauses decode;
- if the last committed token and KV-cache state are consistent, Embedding may restart or relocate;
- if cache position cannot be proven consistent, destroy the session.

### 18.3 OutputHead Node Failure

If OutputHead node dies:

- PipelineStage output for the current token may be buffered only if edge policy allows it;
- if `can_restart`, restart OutputHead and replay final hidden state for the current token;
- if `can_migrate`, relocate OutputHead only to a node with OutputNorm/LmHead resources ready;
- if logits cannot be reproduced from a committed hidden state, destroy the session.

OutputHead failure must not trigger PipelineStage re-planning unless graph capabilities explicitly allow downstream relocation.

### 18.4 PipelineStage Failure

If PipelineStage2 dies:

- upstream stages must stop sending new hidden states to that edge;
- downstream stages must mark their input as unavailable;
- scheduler must identify the affected block set;
- if `can_restart`, restart PipelineStage2 on the same node and validate KV-cache state;
- if `can_migrate`, move the same PipelineStage service instance to another node with matching TransformerBlock resources;
- if KV-cache state is lost and `can_resume` is false, destroy the session.

PipelineStage failures are usually the hardest to recover because KV-cache state is coupled to sequence progress. Recovery requires descriptor-declared cache semantics, not ad hoc runtime guessing.

### 18.5 Tokenizer Failure

Tokenizer failure before prefill is usually restartable or relocatable because it has little runtime state. Tokenizer failure during decode is recoverable if sampled token ids and tokenizer metadata are already committed. If request formatting state is lost and cannot be reconstructed, the session must fail before producing more tokens.

### 18.6 Sampler Failure

Sampler failure is recoverable if logits for the current step are still available or can be recomputed from committed final hidden state. Random state must be part of sampler state. If deterministic replay is required and RNG state is lost, the session must fail rather than silently produce a different sequence.

### 18.7 Failure Policy Source

Failure policy comes from Execution Capabilities and graph validation:

- services without `can_restart` cannot be restarted;
- services without `can_migrate` cannot be relocated;
- services without `can_resume` cannot resume after losing runtime state;
- edges without replay support cannot retry after downstream failure;
- cache resources without checkpoint support cannot survive node death.

The runtime must prefer explicit failure over implicit fallback.

---

## 19. Runtime Scheduler

Planner builds the graph once. Runtime Scheduler drives the graph over time.

Runtime Scheduler is responsible for:

- starting prefill;
- sequencing decode steps;
- enforcing service state transitions;
- enforcing edge backpressure;
- tracking committed sequence positions;
- coordinating KV-cache ownership;
- retrying allowed operations;
- applying failure policy;
- stopping and destroying sessions.

Runtime Scheduler is not responsible for:

- selecting architecture-specific resources;
- changing service semantics;
- materializing legacy worker roles;
- bypassing graph validation;
- reassigning services unless capabilities allow migration.

### 19.1 Scheduler Inputs

Scheduler consumes:

- validated Execution Graph;
- placement plan;
- service endpoint registry;
- execution capabilities;
- session state;
- request generation policy;
- edge contracts;
- failure policy.

### 19.2 Scheduler Operations

Scheduler operations include:

- configure_service;
- start_prefill;
- commit_prefill;
- start_decode_step;
- commit_decode_step;
- send_edge_message;
- apply_backpressure;
- pause_session;
- resume_session;
- drain_service;
- restart_service;
- relocate_service;
- destroy_session.

Each operation must declare required source state and resulting target state.

### 19.3 Prefill Scheduling

Prefill is scheduled as a bounded pipeline:

```
Tokenizer full prompt
  -> Embedding full sequence
  -> PipelineStage chain full sequence
  -> OutputHead last position
  -> Sampler first token
```

Scheduler must not mark prefill committed until:

- all PipelineStage services have committed KV-cache writes;
- OutputHead logits correspond to the final prompt position;
- Sampler has produced the next token or stop state;
- all edge messages for that step have been acknowledged or safely discarded.

### 19.4 Decode Scheduling

Decode is scheduled as repeated token steps:

```
for each decode step:
  sampled token -> Embedding
  hidden -> PipelineStage chain
  final hidden -> OutputHead
  logits -> Sampler
  commit sampled token
```

Scheduler must track:

- step id;
- input token id;
- position id;
- KV-cache version;
- output token id;
- service execution status per step.

### 19.5 Backpressure

Backpressure is part of correctness, not only performance. If PipelineStage2 queue is full, upstream PipelineStage1 must stop sending hidden states. If OutputHead is slow, final PipelineStage must not overwrite hidden buffers that might be needed for retry.

Backpressure policy is declared per edge:

- block upstream;
- buffer bounded messages;
- drop retryable messages;
- fail session on timeout.

### 19.6 Dynamic Runtime

Future dynamic runtime features require descriptor-owned capabilities. Runtime Scheduler may support dynamic changes only when graph validation says they are legal:

- move service;
- replicate service;
- migrate cache;
- prefetch resources;
- resume from checkpoint;
- drain and replace a node;
- split or merge PipelineStage partitions.

Without explicit capabilities, the graph is static for the session.

---

## 20. Execution Capabilities

Runtime Descriptor must describe not only what exists, but what can be done safely.

Execution Capabilities are declared per service, per resource, and per edge. They are consumed by Planner, Execution Graph Validator, Runtime Scheduler, and Failure Model.

### 20.1 Service Capabilities

Service capabilities include:

- can_move: service can be moved before it starts running;
- can_migrate: service can move while preserving or reconstructing runtime state;
- can_restart: service can restart on the same node;
- can_replicate: multiple equivalent instances can exist;
- can_parallelize: service can split work across instances;
- can_colocate: service can share a node/process with compatible services;
- can_cache: service can keep reusable runtime state;
- can_share: service can share resources with another service;
- can_stream: service can emit partial outputs;
- can_pipeline: service can process pipeline-overlapped inputs;
- can_prefetch: resources can be staged before assignment;
- can_resume: service can continue from committed state after interruption;
- can_checkpoint: service can persist runtime state for recovery.

### 20.2 Resource Capabilities

Resource capabilities include:

- movable: resource can be copied after session creation;
- shareable: one installed copy can serve multiple services;
- shardable: resource can be partitioned;
- replicable: resource can be installed on multiple nodes;
- cacheable: runtime can keep a decoded/dequantized form;
- prefetchable: install can stage resource before placement is final;
- remote_readable: service may consume resource remotely;
- requires_locality: service must have local access;
- checkpoint_participates: resource is part of resumable state.

### 20.3 Edge Capabilities

Edge capabilities include:

- streaming;
- replayable;
- ordered;
- idempotent;
- backpressure_supported;
- timeout_recoverable;
- resumable_after_disconnect;
- zero_copy_when_colocated.

### 20.4 Capability Validation

Capabilities must be internally consistent:

- `can_migrate` requires resource movability or replicated readiness;
- `can_resume` requires checkpoint or reconstructable state;
- `can_stream` requires edge streaming support;
- `can_pipeline` requires ordered edge semantics and backpressure;
- `can_replicate` requires deterministic routing or merge policy;
- `can_share` requires compatible resource layout and lifetime.

Capabilities are promises. If a service declares a capability, tests must prove it. If no test exists, the capability must be false.

---

## 21. Execution Graph Validator

Runtime Descriptor Validator proves that the descriptor is complete and well-formed. Execution Graph Validator proves that the concrete placed graph is executable.

This is a separate entity and a separate gate.

### 21.1 Validator Inputs

Execution Graph Validator consumes:

- Runtime Descriptor;
- concrete Execution Graph;
- placement plan;
- service instance parameters;
- semantic resource assignments;
- edge contracts;
- node capabilities;
- execution capabilities;
- cache layout contract.

### 21.2 Structural Checks

The validator checks:

- required service chain exists;
- graph is connected for the requested inference mode;
- graph has no illegal cycles;
- every service instance has placement;
- every placed service has required resources;
- every edge has one producer and at least one consumer unless broadcast is declared;
- PipelineStage ordering covers the required block set exactly once unless parallel partitioning is declared;
- optional services are connected only through legal edges.

### 21.3 Type and Shape Checks

The validator checks data compatibility:

- Tokenizer output type matches Embedding input type;
- token id dtype and vocabulary range match Embedding expectations;
- Embedding hidden size matches first PipelineStage hidden size;
- Embedding dtype/layout matches PipelineStage input contract;
- PipelineStage output hidden size matches next PipelineStage input hidden size;
- PipelineStage output dtype/layout matches next edge contract;
- final PipelineStage hidden size matches OutputHead input hidden size;
- OutputHead logits vocab size matches Sampler vocabulary metadata;
- OutputHead logits dtype matches Sampler accepted dtype;
- Sampler output token id type matches Tokenizer/client decode contract.

### 21.4 Runtime Metadata Checks

The validator checks execution metadata:

- KV-cache layout matches every PipelineStage service;
- sequence position policy matches Embedding and PipelineStage contracts;
- RoPE parameters/tables match Embedding and PipelineStage consumers;
- attention mask policy is compatible across stages;
- batch size limits match along the graph;
- context length limits match service constraints;
- quantization/dequantization expectations match resource consumers;
- tied embeddings are valid for OutputHead when declared;
- tokenizer vocabulary size equals OutputHead/Sampler vocabulary size.

### 21.5 Placement and Capability Checks

The validator checks:

- node memory can hold assigned runtime resources;
- node compute/device capability satisfies service requirements;
- network budget can support edge data volume;
- colocated shortcuts are legal for the colocated service pair;
- requested recovery policy is supported by capabilities;
- migration/restart/replication claims have required resource support;
- scheduler operations are legal for all service states.

### 21.6 Failure Checks

The validator checks that every service and edge has a declared failure policy:

- fatal;
- retry;
- restart;
- relocate;
- resume;
- cancel session.

If a failure policy references a capability that is not declared and tested, validation fails.

### 21.7 Validator Output

Execution Graph Validator outputs:

- valid/invalid status;
- normalized executable graph id;
- service compatibility report;
- edge compatibility report;
- resource placement report;
- capability report;
- failure policy report;
- human-readable rejection reasons.

Inference can start only from a valid executable graph id.

---

## 22. Validation Before Inference

Before any inference smoke test, including TinyLlama, the system must provide evidence that architecture invariants are intact.

Required validation sequence:

1. Build or load Architecture Descriptor for the concrete model.
2. Produce Runtime Descriptor.
3. Run `validate_runtime_descriptor`.
4. Build concrete Execution Graph.
5. Run Execution Graph Validator.
6. Run Role Planner using only Runtime Descriptor services.
7. Run Install Planner using only semantic resources.
8. Validate installed resources against placement.
9. Configure orchestrator and node-agent services from graph.
10. Start smoke inference only after all previous steps pass.

Minimum descriptor validation for each supported architecture:

- Llama;
- Qwen;
- Gemma;
- Phi;
- SmolLM;
- DeepSeek.

Validation must prove:

- Tokenizer service exists when text input exists;
- Embedding service exists;
- at least one PipelineStage service exists;
- OutputHead service exists;
- Sampler service exists;
- all dependencies exist;
- all semantic resources exist;
- all required resources have storage mappings or generation rules;
- graph input/output types are compatible;
- graph tensor shapes, hidden sizes, vocab sizes, KV layout, and RoPE contracts are compatible;
- every service and edge has explicit failure policy;
- requested scheduler operations are legal for declared capabilities;
- no service requires Entry/Middle/Final;
- no service derives semantics from layer ranges;
- no planner/orchestrator/node-agent/runtime component performs architecture-specific semantic selection.

If validation fails, inference is forbidden.

---

## 23. Test Taxonomy

Tests are grouped by architecture invariant, not by benchmark model.

### 23.1 Descriptor Tests

Required:

- test-runtime-descriptor;
- test-runtime-service;
- test-runtime-schema;
- test-resource-graph;
- test-service-dependencies.

These tests validate descriptor identity, service catalog, resource catalog, dependency graph, schema compatibility, tied resource references, optional resources, and invalid descriptor rejection.

### 23.2 Execution Graph Validator Tests

Required:

- test-execution-graph-validator;
- test-graph-edge-types;
- test-graph-shape-compatibility;
- test-kv-layout-compatibility;
- test-runtime-state-machine;
- test-failure-policy-validation;

These tests prove that the concrete graph is executable, not merely complete. They must reject mismatched Tokenizer/Embedding token contracts, mismatched hidden sizes between Embedding and PipelineStage, mismatched PipelineStage boundaries, mismatched OutputHead/Sampler vocabulary sizes, incompatible KV-cache layouts, incompatible RoPE contracts, missing failure policies, and illegal scheduler transitions.

### 23.3 Planner Tests

Required:

- test-role-assignment;
- test-cost-model;
- test-service-placement.

These tests prove planner consumes services, not legacy roles. They must include grep/static checks or equivalent assertions that planner does not reference Entry, layer_start/layer_end as semantic indicators, or architecture names.

### 23.4 Install Tests

Required:

- test-resource-install;
- test-semantic-blobs;
- test-layout;
- install equivalence;
- resource equivalence.

These tests prove resources are installed where service placement requires them. Layer coverage alone must not be sufficient for readiness.

### 23.5 Runtime Tests

Required:

- test-hidden;
- test-logits;
- test-sampler.

These tests verify service boundary data: hidden states, logits, and sampled tokens. They must run only after descriptor validation.

### 23.6 Architecture Tests

Every architecture must pass Descriptor validation before inference:

- Llama descriptor validation;
- Qwen descriptor validation;
- Gemma descriptor validation;
- Phi descriptor validation;
- SmolLM descriptor validation;
- DeepSeek descriptor validation.

Architecture tests validate descriptor construction only. They do not justify adding architecture branches outside descriptor construction.

### 23.7 Test Execution Ladder

After every implementation phase, tests run in this order:

```
Unit
  -> Integration
      -> Mini Runtime
          -> Smoke
              -> Regression
```

Long E2E and benchmark runs are not allowed before descriptor, planner, install, orchestrator, node-agent, and runtime invariants pass for the phase being tested.

---

## 24. Architecture Decisions

Architecture Decision Records capture decisions that must not be undone accidentally during implementation or future maintenance.

### ADR-001 — Embedding Is an Independent Runtime Service

Decision:

Embedding is a standalone Runtime Service with its own descriptor, resources, dependencies, placement, lifecycle, state, and failure policy.

Context:

Legacy runtime treated embedding as part of Entry or as a side effect of starting at layer zero. That made the first node special and caused planner, install planner, node-agent, and runtime loader code to infer semantics from topology.

Reason:

Embedding has distinct resources, cost profile, data-plane output, and failure behavior. It produces hidden states and does not own tokenization, transformer block execution, output projection, or sampling.

Consequence:

Embedding must never be represented by `worker_role::entry`, `layer_start = 0`, `layer_end = 1`, or first PipelineStage placement. Colocation with the first PipelineStage is allowed only as placement optimization.

### ADR-002 — Planner Does Not Know Model Architectures

Decision:

Planner consumes Runtime Descriptor services, resources, costs, capabilities, and graph templates. Planner must not branch on model family names or tensor names.

Context:

Architecture-specific planner logic makes every new model family a distributed runtime change and encourages hidden special cases.

Reason:

The planner's job is placement and partitioning, not semantic interpretation. Architecture interpretation belongs to Architecture Descriptor and Runtime Descriptor Builder.

Consequence:

No `if llama`, `if qwen`, `if gemma`, `if phi`, `if smollm`, `if deepseek`, or equivalent architecture switch is allowed in planner code.

### ADR-003 — OutputHead Is Never Inferred from the Last Layer

Decision:

OutputHead is a standalone Runtime Service that consumes final hidden states and produces logits.

Context:

Legacy final-worker logic made output projection depend on final pipeline placement. That breaks when OutputHead is colocated elsewhere or replicated independently.

Reason:

OutputHead owns semantic resources such as OutputNorm, LmHead, tied embedding references, logit scale, and vocabulary metadata. These resources are not transformer blocks and are not identified by `layer_end = n_layer`.

Consequence:

The final PipelineStage is not OutputHead. OutputHead may colocate with the final PipelineStage, but only placement can do that. Semantics remain separate.

### ADR-004 — Semantic Resources Replace Entry/Final Blob Bundles

Decision:

Install planning uses semantic resources from Runtime Descriptor, not legacy blob bundles such as Entry, Middle, Final, or Full.

Context:

Previous benchmark failures showed that layer coverage can be ready while semantic resources such as embedding or output head are installed on the wrong node.

Reason:

Install readiness must answer whether every placed service has the resources it needs. Layer coverage alone is insufficient.

Consequence:

Install Planner cannot use Entry/Final placement to decide embedding/output placement. It must derive operations from service placement and semantic resource requirements.

### ADR-005 — Runtime Descriptor Builder Is the Only Descriptor Compiler

Decision:

Runtime Descriptor construction is centralized in Runtime Descriptor Builder.

Context:

If descriptor construction is spread across several files, architecture-specific exceptions will reappear in planner, orchestrator, node-agent, install planner, and runtime loader code.

Reason:

A single builder makes architecture interpretation auditable and testable. It also makes descriptor output deterministic.

Consequence:

Architecture-specific code may produce Architecture Descriptor facts, but normalized Runtime Descriptor semantics must come from the builder. Any code path that creates services/resources outside the builder is an architecture violation.

### ADR-006 — Execution Graph Validator Is a Separate Gate

Decision:

Execution Graph Validator is separate from Runtime Descriptor Validator.

Context:

Descriptor validation can prove that required services and resources exist, but it cannot prove that a concrete placed graph is executable.

Reason:

The executable graph must validate edge type compatibility, hidden sizes, dtype/layout, vocab size, KV-cache layout, RoPE contracts, scheduler operations, state transitions, and failure policies.

Consequence:

Smoke inference is forbidden unless both Runtime Descriptor Validator and Execution Graph Validator pass.

### ADR-007 — Runtime Scheduler Owns Prefill and Decode Progress

Decision:

Runtime Scheduler drives prefill, decode, edge backpressure, service state transitions, retries, recovery, and destroy.

Context:

Planner builds a graph once. It does not manage token-by-token execution. Legacy runtime behavior should not remain the hidden scheduler.

Reason:

Without an explicit scheduler, lifecycle and race conditions become distributed across service handlers and transport code.

Consequence:

Prefill/decode progress, committed sequence positions, KV-cache versions, and failure handling must be scheduler-visible.

### ADR-008 — Failure Recovery Requires Declared Capabilities

Decision:

Restart, migration, replication, resume, streaming, caching, and prefetch are legal only when declared as Execution Capabilities and validated by tests.

Context:

Implicit recovery paths hide data loss and produce non-deterministic behavior.

Reason:

Recovery depends on state ownership, resource locality, edge replay, cache consistency, and deterministic sampler state.

Consequence:

If a capability is not declared and tested, it is false. Runtime must fail explicitly instead of inventing fallback behavior.

---

## 25. Migration Plan

Migration must be incremental. Each phase has a stop point. No phase may silently continue into the next phase.

Every phase has Mandatory Exit Criteria. A phase is incomplete until every criterion is satisfied. If any criterion fails, the next phase is forbidden. Cross-phase edits are forbidden unless the phase explicitly names that subsystem in scope.

### 25.1 Phase 11.1 — Create Runtime Descriptor

Scope:

- introduce Runtime Descriptor model;
- introduce Runtime Service Descriptor model;
- introduce Semantic Resource Descriptor model;
- introduce descriptor validation rules;
- add tests:
  - test-runtime-descriptor;
  - test-runtime-service;
  - test-runtime-schema.

Restrictions:

- no existing runtime behavior changes;
- no planner behavior changes;
- no orchestrator behavior changes;
- no node-agent behavior changes;
- no install planner behavior changes;
- no inference path changes.

Mandatory Exit Criteria:

- descriptor unit tests compile and pass;
- validation rejects malformed descriptors;
- validation passes representative descriptors for Llama, Qwen, Gemma, Phi, SmolLM, and DeepSeek;
- Runtime Descriptor Builder is the only path that constructs Runtime Descriptor objects;
- grep/static guard confirms no planner behavior changed in this phase;
- grep/static guard confirms no install planner behavior changed in this phase;
- grep/static guard confirms no orchestrator behavior changed in this phase;
- grep/static guard confirms no node-agent behavior changed in this phase;
- grep/static guard confirms no runtime execution behavior changed in this phase;
- smoke inference is forbidden.

### 25.2 Phase 11.2 — Convert Role Planner

Scope:

- planner consumes Runtime Descriptor services;
- planner emits service placement;
- remove planner dependency on Entry/Middle/Final as semantics;
- remove planner dependency on layer_start/layer_end as role indicators;
- remove planner architecture branches;
- add old/new comparison tests.

Required tests:

- legacy planner compatibility test;
- new planner service placement test;
- 100% equivalent result test for scenarios where legacy semantics were valid;
- explicit invariant test that embedding is not inferred from Entry or layer zero.

Mandatory Exit Criteria:

- planner static/invariant checks pass;
- old/new comparison passes;
- planner tests pass;
- Execution Graph Validator tests pass for planner-produced graphs;
- grep/static guard confirms planner does not use `worker_role::entry` as a semantic role;
- grep/static guard confirms planner does not use `layer_start == 0` or `layer_end == 1` as Embedding indicators;
- grep/static guard confirms planner does not use `layer_end == n_layer` as OutputHead indicator;
- grep/static guard confirms planner does not branch on architecture names;
- install planner files are unchanged except for test fixtures explicitly needed for planner comparison;
- orchestrator files are unchanged;
- node-agent files are unchanged;
- runtime execution files are unchanged;
- smoke inference is forbidden.

### 25.3 Phase 11.3 — Convert Install Planner

Scope:

- install planner consumes semantic resources from Runtime Descriptor;
- install readiness validates service resource placement;
- remove layer_start/layer_end from non-pipeline resource placement;
- remove Entry/Final resource placement logic.

Required tests:

- install equivalence;
- resource equivalence;
- semantic blob/resource placement;
- readiness fails when Embedding resource is on wrong node even if layer coverage is complete;
- readiness fails when OutputHead resource is on wrong node.

Mandatory Exit Criteria:

- install planner operates only on semantic resources;
- resource coverage and layer coverage are distinct;
- install equivalence tests pass;
- resource equivalence tests pass;
- readiness fails when Embedding resources are installed on the wrong node;
- readiness fails when OutputHead resources are installed on the wrong node;
- grep/static guard confirms install planner does not use Entry/Final placement for semantic resources;
- grep/static guard confirms install planner does not use layer_start/layer_end for non-pipeline resources;
- planner behavior is unchanged in this phase except for integration fixtures explicitly required by install tests;
- orchestrator files are unchanged;
- node-agent files are unchanged;
- runtime execution files are unchanged;
- smoke inference is forbidden.

### 25.4 Phase 11.4 — Convert Orchestrator

Scope:

- orchestrator accepts validated Runtime Descriptor and concrete Execution Graph;
- orchestrator configures services from graph;
- orchestrator does not select semantic resources;
- orchestrator does not branch by architecture;
- orchestrator does not use Entry/layer ranges to configure Embedding or OutputHead.

Required tests:

- session graph validation;
- service configure ordering;
- descriptor validation required before session create;
- failure path when descriptor invalid;
- failure path when install readiness missing semantic resources.

Mandatory Exit Criteria:

- orchestrator cannot start inference without valid descriptor and graph;
- orchestrator contains no semantic resource selection logic;
- session lifecycle tests pass from descriptor validation through worker ready;
- invalid descriptor prevents session create;
- invalid execution graph prevents session create;
- missing semantic resource readiness prevents session create;
- grep/static guard confirms orchestrator does not branch on architecture names;
- grep/static guard confirms orchestrator does not configure Embedding through Entry;
- grep/static guard confirms orchestrator does not configure OutputHead through Final;
- planner files are unchanged;
- install planner files are unchanged except API adaptation explicitly required by orchestrator integration;
- node-agent files are unchanged;
- runtime execution files are unchanged;
- smoke inference is forbidden.

### 25.5 Phase 11.5 — Convert Node Agent

Scope:

- node-agent binds assigned services from graph;
- node-agent receives semantic resource handles for each service;
- node-agent does not substitute roles;
- node-agent does not convert Embedding to Entry;
- node-agent does not infer OutputHead from Final;
- node-agent does not branch by architecture.

Required tests:

- service binding per role;
- invalid binding rejection;
- Embedding service cannot be configured using Entry fallback;
- OutputHead service cannot be configured using Final fallback;
- resource handle validation.

Mandatory Exit Criteria:

- node-agent service binding is descriptor-driven;
- role substitution is removed or fenced in compatibility-only paths not used by inference;
- service binding tests pass for Tokenizer, Embedding, PipelineStage, OutputHead, and Sampler;
- invalid binding rejection tests pass;
- resource handle validation tests pass;
- grep/static guard confirms node-agent does not substitute Embedding through Entry;
- grep/static guard confirms node-agent does not substitute Tokenizer through Entry;
- grep/static guard confirms node-agent does not substitute OutputHead through Final;
- grep/static guard confirms node-agent does not branch on architecture names;
- planner files are unchanged;
- install planner files are unchanged;
- orchestrator semantic behavior is unchanged except API adaptation explicitly required for node-agent configure;
- runtime execution files are unchanged;
- smoke inference is forbidden.

### 25.6 Phase 11.6 — Convert Runtime

Scope:

- runtime loader consumes Runtime Descriptor service/resource contracts;
- runtime removes architecture branches from execution path;
- runtime does not use legacy Entry/Middle/Final to select resources;
- runtime validates service resources before execution;
- runtime executes descriptor-defined graph services.

Required tests:

- hidden-state boundary test;
- logits parity test;
- sampler test;
- descriptor validation before runtime start;
- Llama/Qwen/Gemma/Phi/SmolLM/DeepSeek descriptor validation before inference.

Mandatory Exit Criteria:

- runtime contains no `if architecture` semantic branches;
- runtime contains no Entry substitution for Embedding;
- runtime contains no Final substitution for OutputHead;
- hidden-state boundary tests pass;
- logits parity tests pass;
- sampler tests pass;
- Runtime Scheduler prefill/decode legality tests pass;
- Execution Graph Validator passes for the concrete smoke graph;
- validate_runtime_descriptor passes for Llama, Qwen, Gemma, Phi, SmolLM, and DeepSeek;
- install readiness passes for semantic resources and pipeline resources separately;
- planner files are unchanged;
- install planner files are unchanged;
- orchestrator files are unchanged except integration needed to call descriptor runtime;
- node-agent files are unchanged except integration needed to bind descriptor runtime;
- first smoke test may be requested only after the global acceptance checklist passes.

---

## 26. Acceptance Checklist Before First Smoke Test

Before running even TinyLlama:

- all new descriptor unit tests compile;
- test-runtime-descriptor passes;
- test-runtime-service passes;
- test-runtime-schema passes;
- validate_runtime_descriptor passes for Llama;
- validate_runtime_descriptor passes for Qwen;
- validate_runtime_descriptor passes for Gemma;
- validate_runtime_descriptor passes for Phi;
- validate_runtime_descriptor passes for SmolLM;
- validate_runtime_descriptor passes for DeepSeek;
- Execution Graph Validator passes for the concrete smoke graph;
- graph edge type checks pass from Tokenizer through Sampler;
- hidden size, dtype, vocab size, KV layout, and RoPE compatibility checks pass;
- service and edge state machines have legal initial transitions;
- Runtime Scheduler can legally execute prefill and decode operations for the graph;
- failure policies are declared for every service and edge;
- planner has no direct dependency on Entry as semantic role;
- planner does not use layer_start/layer_end as Embedding or OutputHead indicators;
- planner does not branch on architecture names;
- orchestrator contains no semantic resource selection logic;
- orchestrator does not branch on architecture names;
- node-agent does not substitute Embedding through Entry;
- node-agent does not substitute Tokenizer through Entry;
- node-agent does not substitute OutputHead through Final;
- install planner uses only semantic resources from Runtime Descriptor;
- install readiness checks semantic resources and layer/block resources separately;
- runtime does not contain architecture-specific semantic branches;
- runtime validates descriptor/service/resource contracts before inference;
- no smoke test starts unless the session reaches SESSION_READY through the documented lifecycle.

Only after this checklist passes is the first smoke test allowed.

After the first smoke test passes, longer E2E and benchmark runs may begin in the test ladder order:

```
Unit
  -> Integration
      -> Mini Runtime
          -> Smoke
              -> Regression
```

---

## 27. Static Architecture Guards

The implementation phases should add static or near-static checks to prevent regressions. The exact mechanism can be unit tests, repository checks, lints, or targeted grep tests.

Forbidden outside descriptor construction and compatibility-only tests:

- `worker_role::entry` used as Embedding;
- `worker_role::entry` used as Tokenizer;
- `worker_role::final` used as OutputHead;
- `layer_start == 0` used as Embedding;
- `layer_end == 1` used as Embedding;
- `layer_end == n_layer` used as OutputHead;
- `if llama`;
- `if qwen`;
- `if gemma`;
- `if phi`;
- `if smollm`;
- `if deepseek`;
- `if architecture` in planner/orchestrator/node-agent/runtime execution paths.

Allowed:

- architecture name handling inside Architecture Descriptor construction;
- architecture name reporting in logs or user-facing metadata;
- compatibility tests that assert legacy behavior is not used in descriptor runtime;
- migration-only code fenced behind compatibility flags and excluded from target inference path.

---

## 28. Design Consequences

This architecture changes the development loop. Instead of running a full benchmark to discover that semantic resources were installed on the wrong node, the system fails at descriptor validation, graph validation, planner invariant tests, or install readiness checks.

The expected failure mode becomes:

```
Descriptor invalid
  or Graph invalid
  or Placement invalid
  or Install readiness invalid
```

The forbidden failure mode is:

```
TinyLlama session starts
  -> runtime asks Entry for embedding
  -> resource missing at node
  -> architecture-specific fallback silently changes behavior
```

A correct Runtime Descriptor architecture makes invalid states difficult to express and impossible to execute.

---

## 29. Final Target State

At the end of Task 11:

- no role is hard-bound to Entry;
- planner knows Runtime Services, not model architecture;
- orchestrator knows Execution Graph, not model architecture;
- node-agent binds assigned services, not substituted legacy roles;
- install planner places semantic resources, not entry/final blobs;
- runtime consumes descriptor-defined services/resources, not `if architecture` branches;
- all architecture knowledge lives in Runtime Descriptor construction from Architecture Descriptor;
- inference cannot start until descriptor validation, graph validation, placement validation, and install readiness pass.

The Runtime Descriptor becomes the only place where the system is allowed to know what a model means.
