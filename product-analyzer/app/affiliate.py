def generate_affiliate_url(platform: str, original_url: str, tag: str = "jhbotero-20") -> str:
    platform = platform.lower()
    if not original_url:
        return ""
    if "amazon" in platform:
        separator = "&" if "?" in original_url else "?"
        return f"{original_url}{separator}tag={tag}"
    elif "mercadolibre" in platform or "meli" in platform:
        separator = "&" if "?" in original_url else "?"
        return f"{original_url}{separator}matt_tool={tag}"
    elif "aliexpress" in platform:
        return f"https://s.click.aliexpress.com/e/_{tag}?target_url={original_url}"
    return original_url
