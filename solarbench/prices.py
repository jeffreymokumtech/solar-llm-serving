"""On-demand and typical spot prices (USD/hour, eu-west-1, mid-2026) used for
$/M-token figures. Spot prices move; pass the price you actually paid with
`--price-per-hour` and it is recorded in the run config."""

INSTANCE_PRICES: dict[str, dict[str, float]] = {
    "g5.xlarge": {"on_demand": 1.006, "spot_typical": 0.40, "gpus": 1, "gpu": "A10G 24GB"},
    "g5.2xlarge": {"on_demand": 1.212, "spot_typical": 0.48, "gpus": 1, "gpu": "A10G 24GB"},
    "g5.12xlarge": {"on_demand": 5.672, "spot_typical": 2.20, "gpus": 4, "gpu": "A10G 24GB"},
    "g6.xlarge": {"on_demand": 0.805, "spot_typical": 0.35, "gpus": 1, "gpu": "L4 24GB"},
    "g6e.xlarge": {"on_demand": 1.861, "spot_typical": 0.75, "gpus": 1, "gpu": "L40S 48GB"},
}

# Reference API prices for the cost comparison page (USD per million tokens).
API_REFERENCE = {
    "bedrock qwen3-next-80b (input/output)": (0.15, 1.20),
    "bedrock claude haiku 4.5 (input/output)": (1.00, 5.00),
}


def price_for(instance_type: str, pricing: str = "spot_typical") -> float:
    return INSTANCE_PRICES[instance_type][pricing]
