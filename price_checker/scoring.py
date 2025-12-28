"""
Deal scoring logic for price checker
"""

from typing import Dict, Optional, Tuple

# Deal classifications
DEAL_CLASS_GREAT = "great"
DEAL_CLASS_INSANE = "insane"

# Thresholds
GREAT_DEAL_SCORE = 80
INSANE_DEAL_SCORE = 95

# Trust multipliers by seller tier
TRUST_MULTIPLIERS = {
    'first_party': 1.00,
    'fulfilled': 0.95,
    'marketplace_good': 0.85,
    'marketplace_unknown': 0.70,
}

# Default scoring parameters
DEFAULT_CAP_DISCOUNT = 0.30  # 30% discount = max discount score
DEFAULT_OUTLIER_Z_THRESHOLD = 4.0  # Z-score above which we apply outlier penalty
DEFAULT_OUTLIER_PENALTY = 0.60  # Multiply score by this if suspicious
DEFAULT_MIN_MAD = 1.0  # Minimum MAD to avoid division by tiny numbers


def calculate_trust_multiplier(
    seller_tier: str,
    return_ok: bool = True,
    flags: str = None
) -> float:
    """
    Calculate trust multiplier based on seller attributes.

    Returns 0.0 for untrusted listings (no returns, parts only, etc.)
    """
    # Disqualify bad listings
    if not return_ok:
        return 0.0

    if flags:
        flags_lower = flags.lower()
        if 'parts' in flags_lower or 'repair' in flags_lower:
            return 0.0

    return TRUST_MULTIPLIERS.get(seller_tier, 0.70)


def calculate_z_score(
    price: float,
    median_price: float,
    mad_price: float,
    min_mad: float = DEFAULT_MIN_MAD
) -> float:
    """
    Calculate robust Z-score using MAD.

    Z = (median - price) / (1.4826 * MAD)

    The 1.4826 factor makes MAD comparable to standard deviation for normal distributions.
    Positive Z means price is below median (a deal).
    """
    adjusted_mad = max(mad_price, min_mad)
    return (median_price - price) / (1.4826 * adjusted_mad)


def calculate_discount(price: float, median_price: float) -> float:
    """
    Calculate discount percentage vs median.

    Returns positive value if below median, negative if above.
    """
    if median_price <= 0:
        return 0.0
    return (median_price - price) / median_price


def calculate_deal_score(
    price: float,
    median_price: float,
    mad_price: float,
    seller_tier: str,
    return_ok: bool = True,
    flags: str = None,
    cap_discount: float = DEFAULT_CAP_DISCOUNT,
    outlier_z_threshold: float = DEFAULT_OUTLIER_Z_THRESHOLD,
    outlier_penalty: float = DEFAULT_OUTLIER_PENALTY
) -> Tuple[int, str, Dict]:
    """
    Calculate overall deal score (0-100).

    Returns:
        Tuple of (score, deal_class, details)
        - score: 0-100 integer
        - deal_class: "great", "insane", or None if not a deal
        - details: dict with discount, z_score, trust, etc.
    """
    # Calculate components
    trust = calculate_trust_multiplier(seller_tier, return_ok, flags)
    discount = calculate_discount(price, median_price)
    z_score = calculate_z_score(price, median_price, mad_price)

    # If trust is 0, no deal
    if trust == 0:
        return 0, None, {
            'discount': discount,
            'z_score': z_score,
            'trust': trust,
            'outlier_penalty': 1.0,
            'reason': 'untrusted_listing'
        }

    # If price is at or above median, no deal
    if discount <= 0:
        return 0, None, {
            'discount': discount,
            'z_score': z_score,
            'trust': trust,
            'outlier_penalty': 1.0,
            'reason': 'above_median'
        }

    # Clamp discount contribution (cap_discount = 100% score)
    discount_factor = min(discount / cap_discount, 1.0)

    # Outlier penalty for suspiciously cheap items from less trusted sellers
    outlier_mult = 1.0
    if z_score > outlier_z_threshold and trust < 0.90:
        outlier_mult = outlier_penalty

    # Final score
    raw_score = 100.0 * discount_factor * trust * outlier_mult
    score = int(round(raw_score))
    score = max(0, min(100, score))  # Clamp to 0-100

    # Classify the deal
    if score >= INSANE_DEAL_SCORE:
        deal_class = DEAL_CLASS_INSANE
    elif score >= GREAT_DEAL_SCORE:
        deal_class = DEAL_CLASS_GREAT
    else:
        deal_class = None

    return score, deal_class, {
        'discount': discount,
        'discount_pct': f"{discount * 100:.1f}%",
        'z_score': z_score,
        'trust': trust,
        'outlier_penalty': outlier_mult,
        'savings': median_price - price,
    }


def format_deal_embed_fields(
    price: float,
    median_price: float,
    score: int,
    details: Dict,
    condition: str = 'new'
) -> Dict:
    """
    Format deal details for a Discord embed.

    Returns dict with field names and values.
    """
    savings = details.get('savings', 0)
    discount_pct = details.get('discount_pct', '0%')

    return {
        'price': f"${price:.2f}",
        'median': f"${median_price:.2f}",
        'savings': f"${savings:.2f} ({discount_pct})",
        'score': f"{score}/100",
        'condition': condition.title(),
    }


def get_deal_emoji(score: int, deal_class: str = None) -> str:
    """Get emoji for deal score"""
    if deal_class == DEAL_CLASS_INSANE:
        return "🔥"
    elif deal_class == DEAL_CLASS_GREAT:
        return "💰"
    elif score >= 60:
        return "✨"
    elif score >= 40:
        return "👍"
    else:
        return "📊"


def get_deal_color(score: int, deal_class: str = None) -> int:
    """Get Discord embed color for deal score"""
    if deal_class == DEAL_CLASS_INSANE:
        return 0xFF4500  # Orange red
    elif deal_class == DEAL_CLASS_GREAT:
        return 0x2ECC71  # Green
    elif score >= 60:
        return 0x3498DB  # Blue
    elif score >= 40:
        return 0xF1C40F  # Yellow
    else:
        return 0x95A5A6  # Gray
