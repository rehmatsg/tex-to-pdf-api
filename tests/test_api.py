from fastapi.testclient import TestClient
from app.main import app
from app.models.compile import CompileResult
from unittest.mock import patch, MagicMock
from pathlib import Path

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

@patch("app.api.routes_compile.compile_latex_sync")
def test_compile_sync_file(mock_compile):
    # Mock successful compilation
    mock_result = MagicMock()
    mock_result.success = True
    # Use a mock for pdf_path so we can mock exists()
    mock_pdf_path = MagicMock()
    mock_pdf_path.exists.return_value = True
    mock_result.pdf_path = mock_pdf_path
    
    mock_result.compile_time_ms = 100
    mock_compile.return_value = mock_result
    
    # Mock file open
    # We need to mock open() because the route tries to open result.pdf_path
    with patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = b"PDF CONTENT"
        
        response = client.post(
            "/compile/sync",
            files={"file": ("test.tex", b"content", "text/plain")}
        )
        
        assert response.status_code == 200
        assert response.content == b"PDF CONTENT"

@patch("app.api.routes_compile.compile_latex_sync")
def test_compile_sync_code(mock_compile):
    # Mock successful compilation
    mock_result = MagicMock()
    mock_result.success = True
    # Use a mock for pdf_path so we can mock exists()
    mock_pdf_path = MagicMock()
    mock_pdf_path.exists.return_value = True
    mock_result.pdf_path = mock_pdf_path
    
    mock_result.compile_time_ms = 100
    mock_compile.return_value = mock_result
    
    with patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = b"PDF CONTENT"
        
        response = client.post(
            "/compile/sync",
            data={"code": r"\documentclass{article}..."}
        )
        
        assert response.status_code == 200
        assert response.content == b"PDF CONTENT"

def test_compile_sync_missing_input():
    response = client.post("/compile/sync")
    assert response.status_code == 400
    assert "Either 'file' or 'code'" in response.json()["detail"]

@patch("app.api.routes_compile.compile_latex_sync")
def test_validate_success(mock_compile):
    mock_result = CompileResult(
        success=True,
        pdf_path=None,
        compile_time_ms=120,
        log="Log output\nLaTeX Warning: Something minor",
        error_message=None,
        log_truncated=False,
        warnings=["LaTeX Warning: Something minor"],
        errors=[],
    )
    mock_compile.return_value = mock_result

    response = client.post(
        "/compile/validate",
        json={"code": r"\documentclass{article}\begin{document}Hello\end{document}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["compilable"] is True
    assert body["warnings"] == ["LaTeX Warning: Something minor"]
    assert body["errors"] == []

@patch("app.api.routes_compile.compile_latex_sync")
def test_validate_failure(mock_compile):
    mock_result = CompileResult(
        success=False,
        pdf_path=None,
        compile_time_ms=95,
        log="! Undefined control sequence.\n",
        error_message="Undefined control sequence.",
        log_truncated=False,
        warnings=[],
        errors=["Undefined control sequence."],
    )
    mock_compile.return_value = mock_result

    response = client.post(
        "/compile/validate",
        json={"code": r"\documentclass{article}\begin{document}\unknowncmd\end{document}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["compilable"] is False
    assert "Undefined control sequence." in body["errors"][0]

def test_validate_missing_code():
    response = client.post("/compile/validate", json={"code": ""})
    assert response.status_code == 400
    assert "'code' must be provided" in response.json()["detail"]
