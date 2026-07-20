# tup-cloud — OpenCADStudio deployment (RESOLVED 2026-07-20)

## Outcome

Option **(B)** was executed: the whole tup-cloud stack now runs on `monster`
(`eren@192.168.1.104`), removing the under-provisioned Rancher Desktop VM from
the critical path entirely.

- Project lives in `~/tup-cloud` on monster (rsynced from the Mac, including
  the gitignored `.env`).
- `docker compose up -d --build` succeeded there; all 6 services healthy
  (postgres, redis, backend, collabora, opencad, frontend).
- The prebuilt `opencad-wopi` image (compiled on monster earlier) is used by
  the `opencad` service — no local wasm compile anywhere.
- Backend connected as `@Tup007Bot`; drives `VFS` and `GBG` registered;
  observer enabled.
- Access URL: **http://192.168.1.104:8080** (verified 200 from the LAN;
  `/api/health` ok; `/OpenCADStudio/` serves 200).

## History / root cause (kept for reference)

The OpenCADStudio integration was code-complete but could not run on the Mac:
the Rancher Desktop VM was memory-starved — the Rust/wasm build got OOM-killed
and even `docker load` of the 50 MB image wedged the daemon. The identical
build on monster finished in 4m00s, confirming the code was fine and the VM
was the sole constraint. Full details in git history of this file.

## Remaining manual check

- End-to-end CAD round-trip (needs an interactive browser login):
  open a `.dwg`/`.dxf` at http://192.168.1.104:8080 → studio auto-opens the
  file → edit → Ctrl+S → file saved back to the drive with the previous
  revision kept as a version.
- The Mac stack is intentionally left down; monster is now the deployment
  host. To redeploy after code changes: rsync the repo to
  `eren@192.168.1.104:~/tup-cloud` and run `docker compose up -d --build`
  there.
