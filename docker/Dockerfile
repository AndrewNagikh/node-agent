# Deployment image: one container is one machine's role in the cluster --
# either the orchestrator or a node agent. Pick with the entrypoint argument.
#
# Distinct from llama.cpp/tools/distributed/docker/, which is a single-host
# test harness: it runs all three nodes side by side on a bridge network and
# bakes in the verify tools. This one is for putting a real node on a real
# machine.
#
# Build context is the repository root:
#   docker build -f docker/Dockerfile -t node-agent .

ARG UBUNTU_VERSION=24.04
FROM docker.io/ubuntu:${UBUNTU_VERSION} AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc-14 g++-14 cmake build-essential git libssl-dev \
    && rm -rf /var/lib/apt/lists/*

ENV CC=gcc-14 CXX=g++-14

WORKDIR /src
COPY llama.cpp/ ./

# CPU build. GGML_NATIVE=OFF because the image may well run on a different
# CPU than the one that built it -- -march=native would produce instructions
# the target does not have.
RUN cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF \
        -DLLAMA_BUILD_SERVER=OFF \
        -DLLAMA_BUILD_APP=OFF \
        -DLLAMA_BUILD_UI=OFF \
        -DLLAMA_DISTRIBUTED=ON \
        -DGGML_NATIVE=OFF \
    && cmake --build build -j"$(nproc)" \
        --target orchestrator node_agent split_gen3_a split_gen3_b split_gen3_c

RUN mkdir -p /out/lib \
    && find build -name "*.so*" -exec cp -P {} /out/lib \; \
    && cp build/bin/orchestrator build/bin/node_agent \
          build/bin/split_gen3_a build/bin/split_gen3_b build/bin/split_gen3_c /out/

FROM docker.io/ubuntu:${UBUNTU_VERSION} AS runtime

# iproute2 is not decoration: the entrypoint uses `ip route get` to find this
# machine's own address, so the node can advertise something other machines
# can actually reach.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 libssl3 ca-certificates curl iproute2 python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /out/lib/ /app/
COPY --from=build /out/orchestrator /out/node_agent \
     /out/split_gen3_a /out/split_gen3_b /out/split_gen3_c /app/
COPY llama.cpp/tools/distributed/download_model.py /app/download_model.py
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV LD_LIBRARY_PATH=/app
ENV PATH=/app:$PATH
ENV MODELS_DIR=/data/models

# Layers live here. Mount it from the host -- an anonymous volume would be
# discarded with the container, and re-downloading a model's shards is the one
# cost this project exists to avoid paying twice.
RUN mkdir -p /data/models
VOLUME /data/models

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["node"]
