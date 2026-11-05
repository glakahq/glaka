"""Deliver a decided reply through the channel the enquiry came in on."""

import logging

from glaka.mail import MailClient
from glaka.models import Enquiry

logger = logging.getLogger(__name__)


def deliver(enquiry: Enquiry, mail: MailClient) -> None:
    """Send enquiry.final_reply. Web enquiries have nowhere to go yet (the chat widget is step 7).

    Raises MailError if the email could not be sent.
    """
    if enquiry.channel != "email" or not enquiry.final_reply:
        return
    sent = mail.send_reply(
        thread_id=enquiry.external_thread_id or "",
        to=enquiry.customer_email or "",
        subject=enquiry.subject or "",
        body=enquiry.final_reply,
        in_reply_to=enquiry.message_id_header or "",
        references=enquiry.references_header or "",
    )
    enquiry.sent_external_id = sent.id
