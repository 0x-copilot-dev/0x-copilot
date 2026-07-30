{
    # node-gyp build config for the workspace-fs Node-API addon.
    #
    # Node-API ONLY (src/workspace_fs.c includes node_api.h and nothing else),
    # so ONE binary is ABI-stable across Node and Electron: build.mjs builds it
    # with the plain Node headers and the SAME .node loads in the Electron main
    # process (verified: Node 25 / modules=141 build loaded under Electron 43 /
    # modules=148). Do not add a V8 / nan / node.h dependency — that would make
    # the artifact per-runtime and force an electron-rebuild step.
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
                                "AdditionalOptions": [
                                    "/std:c11",
                                    "/W4",
                                    "/sdl",
                                    "/utf-8",
                                ],
                                "BufferSecurityCheck": "true",
                                "ControlFlowGuard": "Guard",
                            },
                            "VCLinkerTool": {
                                "RandomizedBaseAddress": "2",
                                "DataExecutionPrevention": "2",
                                "ImageHasSafeExceptionHandlers": "true",
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
