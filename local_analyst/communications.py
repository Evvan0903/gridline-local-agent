"""Deterministic evidence linkage and drafts, not automatic factual inference."""

from decimal import Decimal
import pandas as pd


def link_messages(messages, evidence, message_entity, evidence_entity, text_column, topic_rules,
                  amount_column="impact", evidence_id_column="evidence_id"):
    required = {message_entity, text_column}
    if not required.issubset(messages) or not {evidence_entity, amount_column, evidence_id_column, "topic"}.issubset(evidence):
        raise ValueError("Supply explicit message/evidence field mappings")
    rows = []
    for index, message in messages.iterrows():
        text = str(message[text_column])
        topics = [topic for topic, words in topic_rules.items() if any(word.lower() in text.lower() for word in words)]
        subset = evidence[evidence[evidence_entity].eq(message[message_entity]) & evidence.topic.isin(topics)]
        impact = max((abs(Decimal(str(v))) for v in subset[amount_column].dropna()), default=Decimal(0))
        rows.append({"message_source_row": int(message.get("__source_row", index + 2)),
                     "entity": message[message_entity], "message": text, "topics": ", ".join(topics),
                     "linked_evidence_ids": ", ".join(subset[evidence_id_column].astype(str)),
                     "financial_exposure": impact,
                     "link_strength": "entity_and_topic_support_only" if len(subset) else "no_transaction_match",
                     "classification": "needs_human_review" if len(subset) else "unresolved_assumption"})
    return pd.DataFrame(rows).sort_values("financial_exposure", ascending=False, kind="stable")


def draft_response(row, evidence_summary, rule_reference):
    if not row["linked_evidence_ids"]:
        return "We have not matched this message to transaction evidence. Please identify the account or statement line; no adjustment has been assumed."
    return (f"Thanks for raising this. We linked your question to evidence {row['linked_evidence_ids']}. "
            f"{evidence_summary} The governing reference is {rule_reference}. "
            "This is a calculation review, not a comparison with an actual paid statement. "
            "Any exception to the written plan needs an explicit decision. We have not assumed an adjustment or changed your payment.")
