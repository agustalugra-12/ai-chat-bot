"""Cloudinary image / raw file upload helper."""
import os
import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
    secure=True,
)


def upload_image(file_bytes: bytes, folder: str = "pelangi/kb") -> dict:
    """Upload image bytes to Cloudinary; returns {url, public_id, format, bytes}."""
    res = cloudinary.uploader.upload(
        file_bytes,
        folder=folder,
        resource_type="image",
        overwrite=False,
        transformation=[{"quality": "auto", "fetch_format": "auto"}],
    )
    return {
        "url": res["secure_url"],
        "public_id": res["public_id"],
        "format": res.get("format"),
        "bytes": res.get("bytes"),
        "width": res.get("width"),
        "height": res.get("height"),
    }


def upload_raw(file_bytes: bytes, filename: str, folder: str = "pelangi/docs") -> dict:
    """Upload non-image (PDF/DOCX) as 'raw'."""
    res = cloudinary.uploader.upload(
        file_bytes,
        folder=folder,
        resource_type="raw",
        public_id=filename.rsplit(".", 1)[0],
        use_filename=True,
        unique_filename=True,
        overwrite=False,
    )
    return {
        "url": res["secure_url"],
        "public_id": res["public_id"],
        "format": res.get("format"),
        "bytes": res.get("bytes"),
    }


def delete_asset(public_id: str, resource_type: str = "image") -> None:
    try:
        cloudinary.uploader.destroy(public_id, resource_type=resource_type, invalidate=True)
    except Exception:
        pass
