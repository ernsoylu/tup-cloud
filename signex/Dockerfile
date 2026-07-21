# Signex EDA (Apache-2.0, https://github.com/alplabai/signex)
# built from source to wasm — self-hosted, no dependency on their site.
# Upstream ships no web target; web.patch is our port (target-gated deps,
# WebGL2 fallback, web-safe Instant, trunk scaffold) and wopi.patch adds the
# tup-cloud WOPI-style load/save for embedded sessions
# (?wopi=…&access_token=… → auto-open + Ctrl+S saves back to the host).
# Scope matches upstream: .snxsch opens and saves; .snxpcb opens read-only
# (the PCB editor has no save path in v0.14 even on desktop).
FROM rust:1-bookworm AS build
ARG SNX_REPO=https://github.com/alplabai/signex.git
# Pin to a release tag; bump deliberately and re-test the two patches.
ARG SNX_REF=v0.14.0

RUN rustup target add wasm32-unknown-unknown

# Prebuilt trunk binary when available for this arch; source install as fallback.
RUN ARCH="$(uname -m)" \
    && (curl -fsSL "https://github.com/trunk-rs/trunk/releases/latest/download/trunk-${ARCH}-unknown-linux-gnu.tar.gz" \
        | tar xz -C /usr/local/bin trunk) \
    || cargo install trunk --locked

WORKDIR /build
RUN git clone --depth 1 --branch "${SNX_REF}" "${SNX_REPO}" .

COPY web.patch wopi.patch /tmp/
RUN git apply --stat /tmp/web.patch /tmp/wopi.patch \
    && git apply /tmp/web.patch /tmp/wopi.patch

# getrandom 0.3 needs its wasm backend selected via cfg on top of the
# crate-level `wasm_js` feature the patch enables.
ENV RUSTFLAGS='--cfg getrandom_backend="wasm_js"'
RUN trunk build --release --public-url /Signex/

FROM nginx:alpine
COPY --from=build /build/dist /usr/share/nginx/html/Signex
