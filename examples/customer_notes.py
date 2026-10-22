"""Data Designer config that generates customer support notes full of fake PII.

The notes feed nemo_helix_privacy_pipeline: the Anonymizer redacts the PII in
the note column, and Safe Synthesizer can then learn from the redacted table.
Regenerate the spec the DAG submits with `make privacy-spec`.
"""

import data_designer.config as dd

PROVIDER = "default/ollama"
MODEL = "gemma4:e4b-mlx"


def load_config_builder() -> dd.DataDesignerConfigBuilder:
    """Build the Data Designer config.

    Returns:
        A config builder with a Faker person, a few categories, and an LLM note
        that mentions the person's details.
    """
    model_configs = [dd.ModelConfig(provider=PROVIDER, model=MODEL, alias="text")]
    builder = dd.DataDesignerConfigBuilder(model_configs)
    builder.add_column(
        dd.SamplerColumnConfig(
            name="id", sampler_type=dd.SamplerType.UUID, params=dd.UUIDSamplerParams()
        )
    )
    builder.add_column(
        dd.SamplerColumnConfig(
            name="customer",
            sampler_type=dd.SamplerType.PERSON_FROM_FAKER,
            params=dd.PersonFromFakerSamplerParams(age_range=[21, 80]),
            drop=True,
        )
    )
    builder.add_column(
        dd.SamplerColumnConfig(
            name="product",
            sampler_type=dd.SamplerType.CATEGORY,
            params=dd.CategorySamplerParams(values=["laptop", "phone", "router"]),
        )
    )
    builder.add_column(
        dd.SamplerColumnConfig(
            name="plan",
            sampler_type=dd.SamplerType.CATEGORY,
            params=dd.CategorySamplerParams(values=["basic", "plus", "business"]),
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
        dd.ExpressionColumnConfig(name="age", expr="{{ customer.age }}", dtype="int")
    )
    builder.add_column(
        dd.ExpressionColumnConfig(name="state", expr="{{ customer.state }}")
    )
    builder.add_column(
        dd.LLMTextColumnConfig(
            name="note",
            prompt=(
                "Write a two-sentence {{ sentiment }} support agent note about a "
                "customer on the {{ plan }} plan with a {{ product }} problem. "
                "Mention the customer's full name {{ customer.first_name }} "
                "{{ customer.last_name }}, their email {{ customer.email_address }}, "
                "their phone number {{ customer.phone_number }}, and that they live "
                "in {{ customer.city }}. Return only the note."
            ),
            model_alias="text",
        )
    )
    return builder
