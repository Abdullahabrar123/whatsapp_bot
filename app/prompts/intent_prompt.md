Classify and answer the customer's latest message.

Allowed intents:
{intents}

Guidance:
- greeting: an opening hello with no other request.
- pricing_inquiry: asks what something costs.
- availability: asks whether something/someone is free.
- booking_request: wants to make, move or confirm an appointment.
- order_status: asks about an existing order or job.
- complaint: expresses dissatisfaction, however politely.
- refund_request: asks for money back.
- cancellation: wants to cancel something.
- location_hours: asks where you are or when you are open.
- human_agent_request: asks for a person.
- opt_in / opt_out: asks to start or stop receiving messages.
- unknown: anything you cannot confidently place.

Set confidence below {escalate_threshold} whenever you are unsure, the question
is ambiguous, or the knowledge does not clearly answer it. A low score is
correct and safe; a confident wrong answer is not.
