from agents.file_index_models import FileEntry, MethodEntry


def test_file_entry_merge_prefers_source_metadata_and_completes_span() -> None:
    endpoint = MethodEntry(
        qualified_name="pkg.run",
        start_line=0,
        end_line=8,
        node_type="METHOD",
    )
    analyzed = MethodEntry(
        qualified_name="pkg.run",
        start_line=3,
        end_line=0,
        node_type="FUNCTION",
        content_hash="abc123",
    )

    merged = FileEntry(methods=[endpoint]).merge_from(FileEntry(methods=[analyzed]))

    assert merged.methods == [
        MethodEntry(
            qualified_name="pkg.run",
            start_line=3,
            end_line=8,
            node_type="FUNCTION",
            content_hash="abc123",
        )
    ]


def test_file_entry_merge_does_not_change_existing_node_kind_without_source_metadata() -> None:
    existing = FileEntry(
        methods=[MethodEntry(qualified_name="pkg.Service", start_line=1, end_line=10, node_type="CLASS")]
    )
    duplicate = FileEntry(
        methods=[MethodEntry(qualified_name="pkg.Service", start_line=1, end_line=10, node_type="METHOD")]
    )

    existing.merge_from(duplicate)

    assert existing.methods[0].node_type == "CLASS"


def test_file_entry_merge_sorts_and_takes_deep_copy_ownership() -> None:
    source = FileEntry(
        methods=[
            MethodEntry(qualified_name="pkg.later", start_line=20, end_line=25, node_type="FUNCTION"),
            MethodEntry(qualified_name="pkg.earlier", start_line=2, end_line=5, node_type="FUNCTION"),
        ],
        content_hash="file123",
    )

    merged = FileEntry().merge_from(source)
    source.methods[1].qualified_name = "changed"

    assert merged.content_hash == "file123"
    assert [method.qualified_name for method in merged.methods] == ["pkg.earlier", "pkg.later"]
    assert merged.methods[0] is not source.methods[1]
