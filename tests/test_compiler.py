import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from app.services.latex_compiler import compile_latex_sync
from app.models.compile import CompileOptions

@pytest.fixture
def mock_subprocess():
    with patch("subprocess.run") as mock:
        yield mock

@pytest.fixture
def mock_temp_dir():
    with patch("tempfile.mkdtemp") as mock:
        mock.return_value = "/tmp/mock_latex_job"
        yield mock

@pytest.fixture
def mock_shutil():
    with patch("shutil.copy") as mock:
        yield mock

def test_compile_success(mock_subprocess, mock_shutil):
    # Setup mock to return success
    process_mock = MagicMock()
    process_mock.returncode = 0
    process_mock.stdout = "Output log"
    mock_subprocess.return_value = process_mock
    
    # Mock file existence for PDF check
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = True
        
        options = CompileOptions(passes=1)
        # We need a dummy file path that "exists" for the initial check in the service if we added one
        # The service checks source_file_path.suffix
        
        result = compile_latex_sync(Path("dummy.tex"), options)
        
        assert result.success is True
        assert result.log == "--- Pass 1 ---\nOutput log"
        assert result.pdf_path is not None

def test_compile_failure(mock_subprocess, mock_shutil):
    # Setup mock to return failure
    process_mock = MagicMock()
    process_mock.returncode = 1
    process_mock.stdout = "Error log"
    mock_subprocess.return_value = process_mock
    
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = False
        
        options = CompileOptions(passes=1)
        result = compile_latex_sync(Path("dummy.tex"), options)
        
        assert result.success is False
        assert "Error log" in result.log

def test_error_parsing(mock_subprocess, mock_shutil):
    # Setup mock to return failure with specific LaTeX error
    process_mock = MagicMock()
    process_mock.returncode = 1
    process_mock.stdout = "This is pdfTeX...\n! Undefined control sequence.\nl.10 \\foo"
    mock_subprocess.return_value = process_mock
    
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = False
        
        options = CompileOptions(passes=1)
        result = compile_latex_sync(Path("dummy.tex"), options)
        
        assert result.success is False
        assert result.error_message == "Undefined control sequence."

def test_dangerous_macro(mock_subprocess, mock_shutil):
    # Setup mock to return success (should not be reached)
    process_mock = MagicMock()
    mock_subprocess.return_value = process_mock
    
    with patch("pathlib.Path.read_text") as mock_read:
        # Simulate dangerous content
        mock_read.return_value = r"\documentclass{article}\begin{document}\write18{rm -rf /}\end{document}"
        
        options = CompileOptions(passes=1)
        
        # The service catches exceptions and returns a result
        result = compile_latex_sync(Path("evil.tex"), options)
        
        assert result.success is False
        assert "Dangerous macro detected" in result.log

def test_missing_pdflatex(mock_subprocess, mock_shutil):
    # Setup mock to raise FileNotFoundError
    mock_subprocess.side_effect = FileNotFoundError
    
    with patch("pathlib.Path.exists") as mock_exists:
        mock_exists.return_value = False
        
        options = CompileOptions(passes=1)
        result = compile_latex_sync(Path("dummy.tex"), options)
        
        assert result.success is False
        assert "pdflatex binary not found" in result.error_message

def test_compile_zip_success(mock_subprocess):
    # Setup mock to return success
    process_mock = MagicMock()
    process_mock.returncode = 0
    process_mock.stdout = "Output log"
    mock_subprocess.return_value = process_mock
    
    with patch("zipfile.ZipFile") as mock_zip:
        mock_zip_instance = MagicMock()
        mock_zip.return_value.__enter__.return_value = mock_zip_instance
        # Mock namelist to return safe paths
        mock_zip_instance.namelist.return_value = ["main.tex", "image.png"]
        
        with patch("pathlib.Path.exists") as mock_exists:
            # We need to handle multiple exists calls:
            # 1. work_dir / "main.tex" -> True (heuristic)
            # 2. expected_pdf -> True
            mock_exists.side_effect = [True, True, True, True] 
            
            options = CompileOptions(passes=1)
            result = compile_latex_sync(Path("project.zip"), options)
            
            assert result.success is True
            mock_zip_instance.extractall.assert_called_once()

def test_compile_zip_unsafe_path(mock_subprocess):
    with patch("zipfile.ZipFile") as mock_zip:
        mock_zip_instance = MagicMock()
        mock_zip.return_value.__enter__.return_value = mock_zip_instance
        # Mock namelist to return UNSAFE paths
        mock_zip_instance.namelist.return_value = ["../evil.tex"]
        
        options = CompileOptions(passes=1)
        
        # The service catches exceptions and returns a result
        result = compile_latex_sync(Path("project.zip"), options)
        
        assert result.success is False
        assert "Invalid path in zip" in result.log
