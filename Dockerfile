# Skeleton: filled in after the stack choice (see docs/PROJECT_MANIFEST.md).
# Shape is fixed: one container, baked image, no bind-mount of code.
ARG BASE_IMAGE=alpine:3.21
FROM ${BASE_IMAGE}
WORKDIR /app
# TODO(stack): runtime, dependencies, code, CMD.
CMD ["tail", "-f", "/dev/null"]
