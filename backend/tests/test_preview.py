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


def test_extracts_and_deduplicates_structured_resources() -> None:
    document = {
        "body_html": '<img src="https://cdn.example/a.png#one">',
        "body_table": {
            "records": [
                {"file": {"src": "https://cdn.example/a.png#two", "size": 12}},
                {"file": {"src": "https://cdn.example/report.pdf", "size": 20}},
            ]
        },
    }

    resources = extract_resource_candidates(document)

    assert [item.normalized_url for item in resources] == [
        "https://cdn.example/a.png",
        "https://cdn.example/report.pdf",
    ]
    assert resources[0].type == "image"
    assert resources[1].declared_size == 20


def test_invalid_sheet_keeps_raw_content_and_marks_partial_preview() -> None:
    result = build_document_preview({"type": "Sheet", "body_sheet": "{not-json"})

    assert "{not-json" in result.html
    assert "SHEET_PARSE_FAILED" in result.issues
