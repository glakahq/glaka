# Demo Bathrooms (fictional)

A made-up bathroom fitting business used for development and demos.
None of these prices, names or policies are real.

    uv run python -m glaka.seed --slug demo-bathrooms --name "Demo Bathrooms"
    uv run python -m glaka.cli ingest --business demo-bathrooms examples/demo-bathrooms/*.md
