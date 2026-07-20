# OpenCADStudio (GPL-3.0, https://github.com/HakanSeven12/OpenCADStudio)
# built from source to wasm — self-hosted, no dependency on their site.
# Mirrors the project's own pages.yml recipe:
#   trunk build --release --public-url /OpenCADStudio/
# (the rust <link> in index.html drops default features, so the wasm-hostile
# solid3d/lzma deps are excluded; no wasm threads → no COOP/COEP needed).
FROM rust:1-bookworm AS build
ARG OCS_REPO=https://github.com/HakanSeven12/OpenCADStudio.git
ARG OCS_REF=main

RUN rustup target add wasm32-unknown-unknown

# Prebuilt trunk binary when available for this arch; source install as fallback.
RUN ARCH="$(uname -m)" \
    && (curl -fsSL "https://github.com/trunk-rs/trunk/releases/latest/download/trunk-${ARCH}-unknown-linux-gnu.tar.gz" \
        | tar xz -C /usr/local/bin trunk) \
    || cargo install trunk --locked

WORKDIR /build
RUN git clone --depth 1 --branch "${OCS_REF}" "${OCS_REPO}" .

# tup-cloud integration: WOPI-style load/save for embedded sessions
# (?wopi=…&access_token=… → auto-open + Ctrl+S saves back to the host).
COPY wopi.patch /tmp/wopi.patch
RUN git apply --stat /tmp/wopi.patch && git apply /tmp/wopi.patch

RUN trunk build --release --public-url /OpenCADStudio/ \
    && echo '[]' > dist/supporters.json

FROM nginx:alpine
COPY --from=build /build/dist /usr/share/nginx/html/OpenCADStudio
