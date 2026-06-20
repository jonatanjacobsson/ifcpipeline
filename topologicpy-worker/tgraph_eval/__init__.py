"""TGraph evaluation suite.

Benchmarks topologicpy's new Python-native ``TGraph`` (v0.9.50+) against the
legacy ``Graph`` API and NetworkX for accuracy, fidelity and speed across
IFC models of different disciplines and sizes.

Run inside the isolated eval image:

    python -m tgraph_eval.run_eval --smoke

See ``README.md`` for the full methodology and model matrix.
"""
