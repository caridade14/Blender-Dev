# Nexo UV development

Use the existing `/workspace/Blender-Dev` checkout. Each cloud task is isolated;
do not create a Git worktree unless the user requests one.

The user requested [morluto/rea](https://github.com/morluto/rea) for artifact
investigation throughout this project. Its checkout is `/workspace/rea` and the
tested CLI is `/workspace/tools/rea-cli/node_modules/.bin/rea` (6.5.0).
See `docs/rea/README.md` for commands, provenance and limits. Use ordinary source
tools for available Python and REA when investigating shipped packages/binaries.
Do not claim MCP registration or native decompilation from the static CLI evidence.

Keep Nexo UV independent of the reference addon: do not copy its code, binaries,
images, branding or promotional text. Preserve the portable bpy/bmesh approach.
Initialize/copy UV flags through the modern `layer.pin`, `vertex_selection` and
`edge_selection` bulk APIs. Creating CustomData can invalidate RNA layer handles;
reacquire layers by name. Avoid legacy flag setters in live UV data iterators.

Run the relevant real Blender smoke tests, not only Python compilation. Preserve
rollback, selection, pins, render UV choice and world-space texel density. Report
actual host/version coverage separately from the configured CI matrix.
Build packages using `python tools/build.py`; keep `dist/` and caches untracked.
