import typer

# 1. The Typer Initialization
app = typer.Typer(
    name="ocp-air",
    help="OpenShift on NVIDIA DSX Air: deploy, operate, and open Console.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def global_setup():
    """Global setup for ocp-air."""
    pass


def main():
    app()


if __name__ == "__main__":
    main()
