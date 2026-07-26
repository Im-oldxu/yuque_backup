from app.modules.preview import build_document_preview, extract_resource_candidates, sanitize_html


def test_sanitize_removes_active_content_and_remote_images() -> None:
    result = sanitize_html(
        '<script>alert(1)</script><img src="https://cdn.example/a.png" onerror="alert(2)">'
        '<a href="javascript:alert(3)">bad</a><iframe src="https://evil.example"></iframe>'
    )

    assert "script" not in result.html.lower()
    assert "onerror" not in result.html.lower()
    assert "javascript:" not in result.html.lower()
    assert "iframe" not in result.html.lower()
    assert "https://cdn.example/a.png" not in result.html
    assert "yb-missing-resource" in result.html


def test_sanitize_rewrites_only_downloaded_resource() -> None:
    result = sanitize_html(
        '<p><img src="https://cdn.example/a.png"></p>',
        local_resources={"https://cdn.example/a.png": "/api/v1/assets/asset-1/content"},
    )

    assert "/api/v1/assets/asset-1/content" in result.html
    assert "yb-missing-resource" not in result.html


def test_extracts_only_explicit_attachments_from_html_and_structured_fields() -> None:
    document = {
        "body_html": (
            '<img src="https://cdn.example/image.png">'
            '<a href="https://www.yuque.com/attachments/manual.pdf#one">manual</a>'
            '<a href="https://cdn.example/report.pdf">external file</a>'
        ),
        "body_table": {
            "records": [
                {"image": {"src": "https://cdn.example/image.png", "size": 12}},
                {
                    "file": {
                        "attachment_url": "https://download.example/private/file.bin",
                        "size": 20,
                    }
                },
                {"file": {"download_url": "https://cdn.example/report.pdf", "size": 30}},
            ]
        },
    }

    resources = extract_resource_candidates(document)

    assert [item.normalized_url for item in resources] == [
        "https://www.yuque.com/attachments/manual.pdf",
        "https://download.example/private/file.bin",
    ]
    assert resources[0].type == "attachment"
    assert resources[1].declared_size == 20


def test_markdown_images_are_not_backup_resources() -> None:
    document = {
        "body": (
            "![one](http://101.69.138.170/202310241749382.png)\n"
            "![two](http://101.69.138.170/202405181523106.png)"
        )
    }

    resources = extract_resource_candidates(document)

    assert resources == []


def test_bare_attachment_url_trims_only_unmatched_closing_delimiters() -> None:
    document = {
        "body": (
            "See (https://www.yuque.com/attachments/archive.zip) and "
            "https://www.yuque.com/attachments/archive_(final).zip and "
            "https://cdn.example/image.png"
        )
    }

    resources = extract_resource_candidates(document)

    assert [item.original_url for item in resources] == [
        "https://www.yuque.com/attachments/archive.zip",
        "https://www.yuque.com/attachments/archive_(final).zip",
    ]


def test_markdown_downloads_only_attachment_links() -> None:
    document = {
        "body": (
            "![diagram](https://cdn.example/diagram.jpg)\n"
            "[manual](https://cdn.example/manual.pdf)\n"
            "![attachment image](https://www.yuque.com/attachments/diagram.jpg)\n"
            "[attachment](https://www.yuque.com/attachments/manual.pdf)\n"
            "[blog](https://www.example.com/posts/backup.html)\n"
            "[article](https://www.example.com/articles/12345)"
        )
    }

    resources = extract_resource_candidates(document)

    assert [item.original_url for item in resources] == [
        "https://www.yuque.com/attachments/manual.pdf"
    ]


def test_command_suffix_is_not_accepted_as_part_of_resource_hostname() -> None:
    resources = extract_resource_candidates(
        {"body": "curl https://mirrors.aliyun.com|grep almalinux"}
    )

    assert resources == []


def test_invalid_sheet_keeps_raw_content_and_marks_partial_preview() -> None:
    result = build_document_preview({"type": "Sheet", "body_sheet": "{not-json"})

    assert "{not-json" in result.html
    assert "SHEET_PARSE_FAILED" in result.issues


def test_preview_skips_blank_body_and_uses_lake_fallback() -> None:
    result = build_document_preview(
        {"type": "Doc", "body": " \n", "body_lake": "Readable lake fallback"}
    )

    assert "Readable lake fallback" in result.html
    assert "PREVIEW_NOT_AVAILABLE" not in result.issues
