```mermaid
graph LR
    Process_Stream_Controller["Process & Stream Controller"]
    Message_Dispatcher_State_Manager["Message Dispatcher & State Manager"]
    LSP_Protocol_Codec["LSP Protocol Codec"]
    Process_Stream_Controller -- "Streams raw server output for logical routing and state updates" --> Message_Dispatcher_State_Manager
    Message_Dispatcher_State_Manager -- "Directs serialized message transmission and manages process lifecycle" --> Process_Stream_Controller
    LSP_Protocol_Codec -- "Dispatches fire-and-forget notifications to the transport" --> Process_Stream_Controller
    LSP_Protocol_Codec -- "Translates domain requests into tracked JSON-RPC transactions" --> Message_Dispatcher_State_Manager
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

The foundational layer responsible for managing the asynchronous I/O streams, process lifecycle, and the low-level serialization of JSON-RPC messages.

### Process & Stream Controller
Manages the physical lifecycle of the Language Server process and the low-level asynchronous pipes, including spawning, stream management, and shutdown.


**Related Classes/Methods**:

- `static_analyzer.engine.lsp_client.LSPClient.start`:96-188
- `static_analyzer.engine.lsp_client.LSPClient._write_message`:588-601



**Source Files:**

- [`static_analyzer/engine/lsp_client.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py)
  - `static_analyzer.engine.lsp_client.LSPClient.__enter__` ([L89-L91](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L89-L91)) - Method
  - `static_analyzer.engine.lsp_client.LSPClient.start` ([L96-L188](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L96-L188)) - Method
  - `static_analyzer.engine.lsp_client.LSPClient._send_notification` ([L579-L586](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L579-L586)) - Method
  - `static_analyzer.engine.lsp_client.LSPClient._write_message` ([L588-L601](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L588-L601)) - Method
  - `static_analyzer.engine.lsp_client.LSPClient._handle_server_request` ([L718-L732](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L718-L732)) - Method


### Message Dispatcher & State Manager
Orchestrates the flow of JSON-RPC messages, tracking pending requests, matching responses to futures, and routing notifications.


**Related Classes/Methods**: _None_


**Source Files:**

- [`static_analyzer/engine/lsp_client.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py)
  - `static_analyzer.engine.lsp_client.LSPClient.did_change` ([L248-L259](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L248-L259)) - Method
  - `static_analyzer.engine.lsp_client.LSPClient._reader_loop` ([L680-L716](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L680-L716)) - Method
  - `static_analyzer.engine.lsp_client.LSPClient._read_single_message` ([L793-L833](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L793-L833)) - Method


### LSP Protocol Codec
Handles serialization and deserialization of data according to the LSP wire format, including header parsing and JSON encoding/decoding.


**Related Classes/Methods**: _None_


**Source Files:**

- [`static_analyzer/engine/lsp_client.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py)
  - `static_analyzer.engine.lsp_client.LSPClient.did_close` ([L261-L269](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/lsp_client.py#L261-L269)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)