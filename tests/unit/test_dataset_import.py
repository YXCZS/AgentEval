from agent_eval_api.dataset_import import DatasetImportFormat, parse_dataset_bytes


def test_csv_parser_handles_quoted_fields_and_nested_json() -> None:
    content = (
        b"id,input,variables,expected_tools,metadata\n"
        b'case-1,"Where is order 42?","{""order_id"": ""42""}",'
        b'"[{""name"": ""search_order"", ""arguments"": {""order_id"": ""42""}}]",'
        b'"{""category"": ""orders""}"\n'
    )

    parsed = parse_dataset_bytes(content, DatasetImportFormat.CSV)

    assert parsed.issues == []
    assert parsed.cases[0].variables == {"order_id": "42"}
    assert parsed.cases[0].expected_tools[0].name == "search_order"


def test_csv_parser_handles_rag_and_tool_extension_fields() -> None:
    content = (
        b"id,input,expected_output,output_schema,expected_tools,expected_state,retrieval_context,messages,metadata\n"
        b'case-1,Find order,shipped,"{""type"": ""object""}",'
        b'"[{""name"": ""search_order"", ""arguments"": {""order_id"": ""42""}}]",'
        b'"{""status"": ""shipped""}",'
        b'"[{""content"": ""Order 42 shipped"", ""document_id"": ""doc-1""}]",'
        b'"[{""role"": ""user"", ""content"": ""Find order""}]",'
        b'"{""category"": ""orders""}\n'
    )

    parsed = parse_dataset_bytes(content, DatasetImportFormat.CSV)

    assert parsed.issues == []
    case = parsed.cases[0]
    assert case.output_schema == {"type": "object"}
    assert case.expected_tools[0].arguments == {"order_id": "42"}
    assert case.expected_state == {"status": "shipped"}
    assert case.retrieval_context[0].document_id == "doc-1"
    assert case.messages[0].role == "user"
    assert case.metadata == {"category": "orders"}


def test_json_and_jsonl_report_row_level_validation_errors() -> None:
    json_parsed = parse_dataset_bytes(
        b'[{"id":"case-1","input":"ok"},{"id":"case-1","input":"duplicate"}]',
        DatasetImportFormat.JSON,
    )
    jsonl_parsed = parse_dataset_bytes(
        b'{"id":"case-1","input":"ok"}\nnot-json\n{"id":"case-2"}\n',
        DatasetImportFormat.JSONL,
    )

    assert [issue.line for issue in json_parsed.issues] == [2]
    assert "duplicate case id" in json_parsed.issues[0].reason
    assert [issue.line for issue in jsonl_parsed.issues] == [2, 3]
    assert len(jsonl_parsed.cases) == 1


def test_csv_invalid_nested_json_and_file_size_are_rejected() -> None:
    invalid = parse_dataset_bytes(
        b'id,input,expected_tools\ncase-1,hello,"not-json"\n', DatasetImportFormat.CSV
    )
    too_large = parse_dataset_bytes(b"12345", DatasetImportFormat.JSON, max_bytes=4)

    assert invalid.cases == []
    assert invalid.issues[0].line == 2
    assert "expected_tools is not valid JSON" in invalid.issues[0].reason
    assert too_large.issues[0].reason == "file exceeds configured size limit"
