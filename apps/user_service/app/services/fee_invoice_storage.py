"""Store and load a fee-invoice PDF in the media R2 bucket."""

from __future__ import annotations

import asyncio

from apps.user_service.app.api.presigned_url import get_r2_client
from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.logger import get_logger

logger = get_logger("fee_invoice_storage")


def fee_invoice_pdf_path(invoice_id: str) -> str:
    """Path for one invoice PDF inside the media bucket."""
    return f"fee-invoices/{invoice_id}.pdf"


async def store_fee_invoice_pdf(invoice_id: str, pdf: bytes) -> str | None:
    """Upload the PDF. A storage failure leaves the invoice in place."""
    pdf_path = fee_invoice_pdf_path(invoice_id)
    try:
        await asyncio.to_thread(_put_pdf, pdf_path, pdf)
    except Exception:
        logger.exception("fee invoice pdf upload failed invoice_id=%s", invoice_id)
        return None
    return pdf_path


async def load_fee_invoice_pdf(pdf_path: str) -> bytes | None:
    """Read a stored invoice PDF. A missing file returns None."""
    try:
        return await asyncio.to_thread(_get_pdf, pdf_path)
    except Exception:
        logger.exception("fee invoice pdf download failed pdf_path=%s", pdf_path)
        return None


def _put_pdf(pdf_path: str, pdf: bytes) -> None:
    """Upload one PDF with the shared R2 client."""
    get_r2_client().put_object(
        Bucket=shared_settings.cloudflare_r2.bucket_name,
        Key=pdf_path,
        Body=pdf,
        ContentType="application/pdf",
    )


def _get_pdf(pdf_path: str) -> bytes:
    """Download one PDF with the shared R2 client."""
    response = get_r2_client().get_object(
        Bucket=shared_settings.cloudflare_r2.bucket_name,
        Key=pdf_path,
    )
    return response["Body"].read()
