{
    # node-gyp build config for the workspace-fs Node-API addon.
    #
    # Node-API, so ONE binary is ABI-stable across Node and Electron: build.mjs
    # builds it with the plain Node headers and the SAME .node loads in the
    # Electron main process (verified: Node 25 / modules=141 build loaded under
    # Electron 43 / modules=148). Do not add a V8 / nan / node.h dependency —
    # that would make the artifact per-runtime and force an electron-rebuild
    # step.
    #
    # One deliberate exception, Windows-only: uv_open_osfhandle. Windows keeps
    # the fd table per CRT instance and this addon does not share a CRT with the
    # host, so a CRT fd minted here is meaningless to node:fs (EBADF on first
    # use). libuv's entry point does the conversion on the host's side. It is a
    # plain C symbol both node.exe and Electron export, so the single-binary
    # property above is unaffected; it is not a V8/node.h dependency.
    "targets": [
        {
            "target_name": "workspace_fs",
            "sources": ["src/workspace_fs.c"],
            # Pin the maximum Node-API surface the source uses. Everything in
            # workspace_fs.c is Node-API v1; declaring 8 documents the ceiling
            # and makes the loader refuse a runtime older than the surface we
            # compiled against instead of failing at first call.
            "defines": ["NAPI_VERSION=8"],
            "conditions": [
                [
                    'OS=="win"',
                    {
                        # NtCreateFile is resolved dynamically via
                        # GetProcAddress, so ntdll.lib is only needed for the
                        # winternl.h types; keep it explicit rather than relying
                        # on a default lib.
                        "libraries": ["ntdll.lib"],
                        "defines": ["_UNICODE", "UNICODE", "WIN32_LEAN_AND_MEAN"],
                        "msvs_settings": {
                            "VCCLCompilerTool": {
                                # /W4 + the standard hardening set. /WX is
                                # deliberately NOT enabled: no MSVC warning log
                                # for this translation unit has ever been read
                                # (it has never been compiled on Windows), and
                                # turning warnings fatal blind would make the
                                # first Windows CI run fail for warning taste
                                # instead of reporting whether the NtCreateFile
                                # walk works. Promote to /WX once a green
                                # windows-latest log exists.
                                #
                                # NO "/std:c11" HERE. node-gyp's common.gypi
                                # puts /std:c++20 on every MSVS target, and MSVC
                                # rejects a C and a C++ standard on one command
                                # line: "error D8016: '/std:c++20' and
                                # '/std:c11' ... are incompatible". The C
                                # standard goes through the MSBuild property
                                # below instead.
                                #
                                # That property alone was NOT enough, and the
                                # first windows-latest run failed on exactly the
                                # D8016 above. The reason is which half of
                                # common.gypi applies: its VCCLCompilerTool
                                # block sets the LanguageStandard *property*
                                # only under `clang==1`, and on the MSVC leg
                                # takes the other branch, which appends the raw
                                # flag "-std:c++20" to AdditionalOptions. A
                                # property cannot override a raw flag — both
                                # reach cl, and D8016 fires. So the inherited
                                # flag has to be removed, not overridden; the
                                # exclusion below is gyp's mechanism for that
                                # (the same "key!" form common.gypi itself uses
                                # to drop inherited -Werror), it matches the
                                # exact string, and gyp's list-filter pass
                                # recurses into msvs_settings and runs after
                                # target defaults are merged, so the flag is
                                # present to be filtered. /Zc:__cplusplus and
                                # /Zm2000 are inherited from the same list and
                                # are deliberately left in place.
                                #
                                # /guard:cf is passed as a raw flag, not via the
                                # named "ControlFlowGuard" setting: the first
                                # windows-latest log showed gyp emitting
                                # "unrecognized setting
                                # VCCLCompilerTool/ControlFlowGuard while
                                # converting to MSBuild" and DROPPING it, so the
                                # hardening it names was never actually applied.
                                "AdditionalOptions": [
                                    "/W4",
                                    "/sdl",
                                    "/utf-8",
                                    "/guard:cf",
                                ],
                                # Drops common.gypi's inherited C++ standard for
                                # this C-only target. Exact-string match on the
                                # spelling common.gypi uses.
                                "AdditionalOptions!": ["-std:c++20"],
                                "LanguageStandard_C": "stdc11",
                                "BufferSecurityCheck": "true",
                            },
                            "VCLinkerTool": {
                                "RandomizedBaseAddress": "2",
                                "DataExecutionPrevention": "2",
                                # NO "ImageHasSafeExceptionHandlers" HERE. It
                                # emits /SAFESEH, and the linker rejects that
                                # outright on this target: "LNK1246: '/SAFESEH'
                                # not compatible with 'x64' target machine".
                                # SafeSEH is an x86-32 mitigation — it exists
                                # because 32-bit exception handler chains live
                                # on the stack and can be overwritten. x64
                                # instead carries unwind and handler data in the
                                # PE's .pdata/.xdata, out of reach of a stack
                                # overwrite, so the property is not "off" here,
                                # it is structurally unnecessary. Restore it
                                # only behind an x86-32 condition, if this addon
                                # is ever built for that architecture.
                                #
                                # CFG needs the linker half too; a /guard:cf
                                # compile alone does not produce a guarded image.
                                "AdditionalOptions": ["/guard:cf"],
                            },
                        },
                    },
                    {
                        # POSIX (macOS + Linux). -Werror is enforced here
                        # because this branch IS compiled in CI on both
                        # platforms, so a warning is actionable rather than
                        # invisible.
                        "cflags": ["-std=c11", "-Wall", "-Wextra", "-Werror"],
                    },
                ],
                [
                    'OS=="mac"',
                    {
                        "xcode_settings": {
                            "GCC_C_LANGUAGE_STANDARD": "c11",
                            "MACOSX_DEPLOYMENT_TARGET": "10.15",
                            "OTHER_CFLAGS": ["-Wall", "-Wextra", "-Werror"],
                        }
                    },
                ],
            ],
        }
    ]
}
