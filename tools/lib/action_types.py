# Neutral home for action-type constants shared by apply.py and
# review_protocol.py. Extracted from apply.py (Phase 7) specifically to avoid
# a circular import: review_protocol.py needs to know which action types have
# a real mechanical auto-implementer, and apply.py needs review_protocol for
# the codex-review auto-implementation path.
IMPLEMENTABLE_ACTIONS = {"title-tag-rewrite", "meta-description-rewrite"}
