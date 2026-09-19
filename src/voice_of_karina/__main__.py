"""Internal skill transport; all human interaction belongs to the agent."""

from __future__ import annotations

import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Annotated, Final

import typer

from voice_of_karina.engine import ErrorResult, run
from voice_of_karina.workflow_models import ErrorDetail

MAX_REQUEST_BYTES: Final = 1_048_576
app: Final = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@app.command()
def main(
    request: Annotated[Path | None, typer.Option("--request", help="JSON request file.")] = None,
) -> None:
    """Read a bounded JSON request from stdin or a file and emit one JSON result."""
    try:
        if request is None:
            raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        else:
            with request.open("rb") as stream:
                raw = stream.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            result = ErrorResult(
                errors=(ErrorDetail(code="request_too_large", message="Request exceeds 1 MiB."),)
            )
        else:
            with redirect_stdout(sys.stderr):
                result = run(raw)
    except OSError as error:
        result = ErrorResult(errors=(ErrorDetail(code="request_read_failed", message=str(error)),))
    typer.echo(result.model_dump_json())
    if result.status in {"failed", "partial"}:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
