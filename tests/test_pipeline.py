from evaluators.basic import evaluate_output
from pipeline.run import run_pipeline


def test_pipeline() -> None:
    output = run_pipeline()
    assert evaluate_output(output) == []
