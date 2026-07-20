# tup-cloud — Open problem & findings (2026-07-20)

## Problem statement

The OpenCADStudio CAD integration is code-complete and **builds successfully**, but
it cannot be **loaded/run on this Mac** because the local Docker VM (Rancher
Desktop / Lima) keeps wedging under memory pressure. Directive: **use `monster`
(remote Ubuntu, `eren@192.168.1.104`) for the build.**

## Root cause (high confidence)

**The Rancher Desktop VM is under-provisioned in memory, and its daemon becomes
fully unresponsive — not just the one operation — when memory is exhausted.**

Two distinct failure modes, one root cause (memory starvation):

1. **Local Rust/wasm build died repeatedly.** `cargo check` for the CAD app
   (iced + wgpu + a DWG/DXF parser — one of the heaviest crate trees in Rust;
   rustc/LLVM peaks at multiple GB per codegen unit × parallelism) was
   OOM-killed. Symptom: build container vanished from `docker ps -a`, produced
   **0 compiled artifacts** (the "312 fingerprints" were downloaded registry
   metadata, not compiled crates; 0 `.rmeta`/`.rlib` in `deps/`). A retry with
   `--memory=6g` reached ~15 crates then stalled again.

2. **`docker load` of the 50 MB image wedged the whole daemon.** Extracting
   ~130 MB of uncompressed layers on the already-pressured VM caused daemon
   thrash: `docker version` and `docker ps` both timed out (20–25 s), while
   `docker load` + `gunzip` sat stuck for 10+ minutes.

### Ruled out
- **Host disk**: 78 GB free on `/` — not the issue.
- **The code**: the *identical* build on `monster` (8 cores, 15 GB RAM / 13 GB
  free) via Docker succeeded: **`Finished release profile [optimized] in
  4m 00s`**, image built and tagged `opencad-wopi`. So the patch and the CAD
  integration compile cleanly; the Mac VM is the sole constraint.

### To confirm the exact number (was interrupted before checking)
Rancher Desktop VM memory allocation:
`~/Library/Application Support/rancher-desktop/settings.json` →
`virtualMachine.memoryInGB`. If it is ≲ 4–6 GB, that is the smoking gun.

## What is already done

- ✅ WOPI patch written into OpenCADStudio (`?wopi=…&access_token=…` → auto-open
  on boot, Ctrl+S saves back via the same WOPI endpoints Collabora uses).
  Patch file: `scratchpad/wopi.patch` (273 lines, 6 files); build recipe:
  `opencad/Dockerfile`.
- ✅ Built on `monster` → image `opencad-wopi` (50 MB). Archive already
  transferred to this Mac: `<scratchpad>/opencad-wopi.tar.gz` (48 MB).
- ✅ Backend endpoints: `POST /api/files/{id}/cad-session`,
  `POST /api/files/{id}/replace`; WOPI `_authorized_entry` hardened with a live
  drive-membership re-check (admins bypass, Redis-cached).
- ✅ Frontend: `CadEditor.tsx`, CAD file-type detection, `api.cadSession`,
  store `cadEntry`; nginx `/OpenCADStudio/` + `/wopi/` routes.
  All type-checked; **not yet rebuilt/deployed**.
- ✅ `docker-compose.yml` `opencad` service set to `image: opencad-wopi` +
  `pull_policy: never` (no local compile).
- ⚠️ Rancher Desktop was restarted (`rdctl shutdown` + relaunch) to recover the
  wedged daemon. **The local tup-cloud stack went down with it** and must be
  brought back up.

## Pending / next steps

1. **Confirm daemon health** after the Rancher restart; `docker compose up -d`
   to restore the stack (postgres/redis/collabora/backend/frontend have
   restart policies but verify).
2. **Fix the VM constraint** (pick one):
   - **(A) Raise Rancher Desktop VM memory to ≥ 8 GB** (Settings →
     Virtual Machine, or `settings.json`), restart, then retry the image load
     (`docker load -i <file>` after gunzip-to-disk, not the streaming pipe).
   - **(B) Relocate the whole tup-cloud stack to `monster`.** It has the
     resources and is on the LAN; run `docker compose up` there and access via
     `http://192.168.1.104:8080`. Removes the Mac VM from the critical path
     entirely. **Recommended** given how consistently this VM fails.
3. Load `opencad-wopi` into whichever Docker runs the stack.
4. Rebuild the **frontend** image (bakes in the new nginx routes + CAD UI).
5. `docker compose up -d opencad frontend`.
6. **Verify** the CAD round-trip: open a `.dwg`/`.dxf` → edits load → Ctrl+S
   in the studio → file saved back to the drive with the previous revision
   kept as a version.

## Decision needed from user

**(A) bump the Mac VM's memory, or (B) move the stack to `monster`?** The build
is done either way; this only decides where the app *runs*.

## Uncommitted work
All of the above (backend endpoints + hardening, frontend CAD UI, compose,
`opencad/`, this file) is uncommitted on `main`.
