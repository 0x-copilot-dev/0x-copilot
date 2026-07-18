{
    "targets": [
        {
            "target_name": "workspace_fs",
            "sources": ["src/workspace_fs.c"],
            "cflags": ["-std=c11", "-Wall", "-Wextra"],
            "conditions": [
                [
                    'OS=="win"',
                    {
                        "libraries": ["-lntdll"],
                        "defines": ["_UNICODE", "UNICODE", "WIN32_LEAN_AND_MEAN"],
                    },
                ],
                [
                    'OS=="mac"',
                    {
                        "xcode_settings": {
                            "GCC_C_LANGUAGE_STANDARD": "c11",
                            "MACOSX_DEPLOYMENT_TARGET": "10.15",
                        }
                    },
                ],
            ],
        }
    ]
}
