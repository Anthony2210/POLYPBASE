"""Business logic for cultures, boxes, transfers, and subculture events.

Keep complex rules out of views and models when they start to grow. For
example, creating a subculture event should update the child box and the audit
trail in one well-named service function.
"""
