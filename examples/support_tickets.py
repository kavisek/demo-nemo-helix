"""Data Designer config that generates synthetic support tickets with the local Ollama model."""

import data_designer.config as dd

PROVIDER = "default/ollama"
MODEL = "gemma4:e4b-mlx"


def load_config_builder() -> dd.DataDesignerConfigBuilder:
    """Build the Data Designer config.

    Returns:
        A config builder with a category sampler and an LLM text column.
    """
    model_configs = [dd.ModelConfig(provider=PROVIDER, model=MODEL, alias="text")]
    builder = dd.DataDesignerConfigBuilder(model_configs)
    builder.add_column(
        dd.SamplerColumnConfig(
            name="product",
            sampler_type=dd.SamplerType.CATEGORY,
            params=dd.CategorySamplerParams(values=["laptop", "phone", "router"]),
        )
    )
    builder.add_column(
        dd.SamplerColumnConfig(
            name="sentiment",
            sampler_type=dd.SamplerType.CATEGORY,
            params=dd.CategorySamplerParams(values=["frustrated", "neutral", "happy"]),
        )
    )
    builder.add_column(
        dd.LLMTextColumnConfig(
            name="ticket",
            prompt="Write a two-sentence {{ sentiment }} customer support ticket about a {{ product }}.",
            model_alias="text",
        )
    )
    return builder
