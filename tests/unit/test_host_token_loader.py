import os
import pytest
from host_observer.token_loader import _read_token_file
TOKEN="synthetic-only-token-01234567890123456789"

@pytest.mark.parametrize("line",[
    "EXECUTOR_SHARED_TOKEN="+TOKEN,
    " export EXECUTOR_SHARED_TOKEN = '"+TOKEN+"' ",
    'EXECUTOR_SHARED_TOKEN="'+TOKEN+'"',
])
def test_literal_token_read_without_output_or_environment_change(tmp_path,capsys,line):
    path=tmp_path/".env"
    path.write_text("# comment\nOTHER=ignored\n"+line+"\n")
    before=path.read_bytes()
    environment=dict(os.environ)
    assert _read_token_file(path)==TOKEN
    assert capsys.readouterr().out=="" and path.read_bytes()==before
    assert dict(os.environ)==environment

@pytest.mark.parametrize("content",[
    "OTHER=unused",
    "EXECUTOR_SHARED_TOKEN=short",
    "EXECUTOR_SHARED_TOKEN=$(touch SHOULD_NOT_EXIST)",
    "EXECUTOR_SHARED_TOKEN="+TOKEN+"\nEXECUTOR_SHARED_TOKEN="+TOKEN,
    "EXECUTOR_SHARED_TOKEN="+TOKEN+" # unsupported comment",
    "EXECUTOR_SHARED_TOKEN",
])
def test_ambiguous_or_nonliteral_input_rejected(tmp_path,content):
    path=tmp_path/".env";path.write_text(content)
    with pytest.raises(ValueError):
        _read_token_file(path)
    assert not (tmp_path/"SHOULD_NOT_EXIST").exists()

def test_unsafe_files_rejected_without_permission_changes(tmp_path):
    path=tmp_path/".env";path.write_text("EXECUTOR_SHARED_TOKEN="+TOKEN)
    path.chmod(0o666)
    with pytest.raises(ValueError):_read_token_file(path)
    assert path.stat().st_mode&0o777==0o666
    path.chmod(0o600)
    linked=tmp_path/"linked";linked.symlink_to(path)
    with pytest.raises(OSError):_read_token_file(linked)
    linked.unlink();os.link(path,linked)
    with pytest.raises(ValueError):_read_token_file(path)

def test_fifo_and_budget_rejected(tmp_path):
    path=tmp_path/"fifo";os.mkfifo(path)
    with pytest.raises(ValueError):_read_token_file(path)
    large=tmp_path/"large";large.write_bytes(b"x"*65537)
    with pytest.raises(ValueError):_read_token_file(large)

def test_existing_group_writable_deployment_is_read_only_compatible(tmp_path):
    path=tmp_path/".env";path.write_text("EXECUTOR_SHARED_TOKEN="+TOKEN)
    path.chmod(0o664)
    before=path.read_bytes()
    assert _read_token_file(path)==TOKEN
    assert path.stat().st_mode&0o777==0o664 and path.read_bytes()==before
