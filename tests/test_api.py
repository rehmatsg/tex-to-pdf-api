from fastapi.testclient import TestClient
from app.main import app
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
