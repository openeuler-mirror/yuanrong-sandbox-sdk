# Python SDK TODO

This file tracks current feature gaps for the Python SDK against the frontend
sandbox v1 HTTP backend. Keep notes focused on current behavior, expected
usage, and backend requirements.

## Current supported surface

The Python SDK supports:

- Sandbox lifecycle: create, delete/kill, detached lifecycle flag.
- Filesystem operations: write/read/exists/list/make_dir/stat/rename/remove,
  resumable file upload/download, directory tar upload/download.
- Command execution: sync commands, long-timeout poll path, background process,
  stdin, wait, kill.
- Persistent shell sessions over HTTP submit/poll.
- Direct frontend `/direct/{sandbox}/...` route for command and file data plane,
  including requestId on direct invoke.
- Reverse tunnel through gateway `/tunnel/{sandbox}`.
- User port forwarding through the sandbox router:
  `http://<gateway>/<safeID>/<port>`.

## Open items

### 1. Create-time cwd

`cwd` is accepted by the SDK create API, but backend create does not currently
apply it to the sandbox process context. Use per-call `cwd` on
`commands.run()` / `shells.create()`.

### 2. Delete by name

`DELETE /sandboxes/{sandboxID}` deletes by sandbox id. Name-based delete needs a
name-to-id lookup route before the SDK can support it reliably.

### 3. Directory manifest copy

Directory copy currently uses tar upload/download. This is fast for successful
single-shot transfers, but it cannot resume from a mid-stream failure.

A future reliable mode should add manifest-based directory copy:

- scan the directory into a manifest of files, directories, size, mode, and mtime;
- transfer large files individually using the resumable file upload/download path;
- optionally pack many small files into bounded tar packs to keep throughput high;
- commit the directory after all entries are verified.

Keep tar as the default for small directories; use manifest mode only when the
caller asks for reliability or the directory is large enough to justify the
extra metadata and per-file scheduling overhead.
