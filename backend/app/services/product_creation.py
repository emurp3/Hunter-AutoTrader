"""Product creation service — generates real product packs from Quick-Cash / Forge opportunities."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any
from sqlmodel import Session, select
from app.models.created_product import CreatedProduct

logger = logging.getLogger(__name__)


# —— Product pack templates ——————————————————————————————————————————————

HUNTER_LEON_PRODUCTS = [
    {
        "name": "Hunter Leon Oxford Brogue — Lion Insignia",
        "design_variant": "lion_insignia",
        "platform": "etsy",
        "manufacturer": "JetPrint",
        "price": 219.00,
        "estimated_margin": 0.52,
        "title": "Hunter Leon Oxford Brogue | Lion Insignia | Luxury Dress Shoe",
        "description": (
            "Handcrafted leather Oxford brogue featuring the iconic Hunter Leon Lion Insignia — "
            "a gold-embossed crowned lion emblem on premium black-to-burgundy gradient leather. "
            "Air-cushioned sole for all-day comfort without sacrificing formality. "
            "Available in US sizes 7–13. Each pair ships in a branded Hunter Leon box. "
            "Perfect for boardrooms, formal occasions, and anyone who refuses to blend in."
        ),
        "price_display": "$219.00",
        "tags": ["oxford shoes", "luxury dress shoes", "mens formal shoes", "brogue shoes",
                 "custom logo shoes", "lion emblem", "hunter leon", "black burgundy shoes",
                 "air sole dress shoes", "premium leather oxford"],
        "image_prompt": (
            "Professional product photography of a black-to-deep-burgundy leather Oxford brogue shoe "
            "on a dark slate surface. Gold embossed lion with crown logo on the vamp. "
            "Air-cushioned transparent sole visible from side angle. Soft rim lighting. "
            "Luxury fashion editorial style."
        ),
        "mockup_prompt": (
            "3D mockup of a leather Oxford brogue dress shoe, black-to-burgundy fade, "
            "with a gold lion crest logo on the side panel. Show both shoes at 3/4 angle "
            "on a dark wood surface with gold accent lighting."
        ),
        "sales_copy": (
            "You already know the difference. Hunter Leon Lion Insignia Oxford — "
            "premium gradient leather, gold lion crest, air sole. Boardroom to black tie. "
            "Only {N} pairs this run."
        ),
        "checkout_recommendation": "Etsy (no monthly fees, built-in audience for luxury goods)",
        "manufacturer_url": "https://www.jetprintapp.com/custom-branding",
        "manufacturer_notes": (
            "JetPrint: upload lion insignia logo as SVG/PNG, select Oxford Leather Dress Shoe SKU, "
            "set placement to vamp panel. Enable branded shoebox (+$1.50). Min order: 1 unit POD."
        ),
        "launch_checklist": [
            "Upload lion_insignia PNG to JetPrint 3D configurator",
            "Generate mockup images (front, side, heel, box)",
            "Create Etsy seller account if not done",
            "List product at $219 with free shipping",
            "Add 8 photos minimum per Etsy SEO requirements",
            "Enable Etsy Ads at $1/day for first 30 days",
        ],
        "source_opportunity": "Hunter Leon Brand — Luxury Footwear",
        "status": "draft",
        "next_action": "Upload logo to JetPrint configurator at jetprintapp.com",
    },
    {
        "name": "Hunter Leon Oxford Brogue — Luxury Monogram",
        "design_variant": "luxury_monogram",
        "platform": "etsy",
        "manufacturer": "JetPrint",
        "price": 199.00,
        "estimated_margin": 0.54,
        "title": "Hunter Leon Oxford Brogue | HL Monogram | Premium Leather Dress Shoe",
        "description": (
            "The signature HL Monogram variant of the Hunter Leon Oxford Brogue. "
            "Deep burgundy suede panel with gold HL architectural monogram. "
            "Black gradient leather body, brogue detailing, air-cushioned sole. "
            "For those who wear their initials where it counts."
        ),
        "price_display": "$199.00",
        "tags": ["monogram shoes", "HL shoe", "luxury oxford", "mens dress shoes",
                 "personalized shoes", "burgundy leather shoes", "hunter leon",
                 "custom brogue", "premium footwear", "air sole oxford"],
        "image_prompt": (
            "Editorial product photo of black Oxford brogue with deep burgundy suede vamp panel. "
            "Gold HL architectural monogram on the panel. Dual-tone black-and-burgundy leather body. "
            "Air sole visible. Moody studio lighting on dark marble surface."
        ),
        "mockup_prompt": (
            "3D mockup pair of Oxford brogues — deep burgundy suede panel on black leather, "
            "gold HL monogram logo, brogue stitching detail. Angled 45-degree product shot, "
            "luxury packaging box open beside them."
        ),
        "sales_copy": (
            "Hunter Leon HL Monogram. The burgundy suede panel. The gold mark. "
            "Everything else is just a shoe."
        ),
        "checkout_recommendation": "Etsy + Shopify (Etsy for discovery, Shopify for brand store)",
        "manufacturer_url": "https://www.jetprintapp.com/custom-branding",
        "manufacturer_notes": (
            "JetPrint: upload HL monogram as SVG, select vamp panel placement on Oxford Leather SKU. "
            "Use heat-transfer vinyl (HTV) method for leather adhesion. Branded box +$1.50."
        ),
        "launch_checklist": [
            "Export HL monogram as clean SVG from design file",
            "Upload to JetPrint and configure vamp placement",
            "Order 1 sample unit ($85-95 cost basis) to verify print quality",
            "Photograph sample for listing images",
            "List on Etsy at $199 with 30-day return policy",
            "Create brand story page on Shopify Hunter Leon store",
        ],
        "source_opportunity": "Hunter Leon Brand — Luxury Footwear",
        "status": "draft",
        "next_action": "Export HL monogram SVG and upload to JetPrint configurator",
    },,
    {
        "name": "Hunter Leon Sneaker — Lion Insignia (White/Black)",
        "design_variant": "lion_insignia_white",
        "platform": "etsy",
        "manufacturer": "Popcustoms",
        "price": 149.00,
        "estimated_margin": 0.56,
        "title": "Hunter Leon Sneaker | Lion Insignia | White Black Leather Luxury Shoe",
        "description": (
            "Crisp white leather low-top with black trim accents and the Hunter Leon Lion Insignia "
            "embossed in black. Clean, sharp, unmistakable. The dress-down piece that still commands "
            "a room. Available US sizes 6–14."
        ),
        "price_display": "$149.00",
        "tags": ["white leather shoes", "lion logo sneaker", "luxury white shoes", "hunter leon",
                 "black trim shoes", "custom logo sneaker", "premium white leather", "mens white dress shoe"],
        "image_prompt": (
            "Product photography of a white leather low-top shoe with black trim detailing. "
            "Matte black lion with crown logo on the side vamp panel. Clean white studio background. "
            "Dramatic side lighting. Premium fashion editorial."
        ),
        "mockup_prompt": (
            "3D mockup of white leather sneaker with black sole trim and tongue edge. "
            "Black lion crest logo on the lateral panel. Pair at 3/4 angle on white marble "
            "with dark shadow. Luxury streetwear aesthetic."
        ),
        "sales_copy": "White leather. Black lion. Hunter Leon knows exactly what it is.",
        "checkout_recommendation": "Etsy (popcustoms.com for POD manufacturing)",
        "manufacturer_url": "https://www.popcustoms.com",
        "manufacturer_notes": (
            "Popcustoms: select Men's Upgraded White Low Top Leather Sneakers. "
            "Upload black lion insignia PNG. Configure lateral panel placement. "
            "Enable branded shoebox. Base cost ~$30-35."
        ),
        "launch_checklist": [
            "Create Popcustoms account",
            "Export lion insignia as black PNG (2000px, transparent bg)",
            "Configure product at popcustoms.com",
            "Generate 3D mockups (8 photos minimum)",
            "List on Etsy at $149 with free shipping",
            "Enable Etsy Ads at $1/day",
        ],
        "source_opportunity": "Hunter Leon Brand — Luxury Footwear",
        "status": "draft",
        "next_action": "Create Popcustoms account and upload black lion insignia PNG",
    },
    {
        "name": "Hunter Leon Sneaker — HL Monogram (White-on-White)",
        "design_variant": "hl_monogram_white",
        "platform": "etsy",
        "manufacturer": "Popcustoms",
        "price": 139.00,
        "estimated_margin": 0.57,
        "title": "Hunter Leon Sneaker | HL Monogram | White on White Leather Luxury Shoe",
        "description": (
            "The most understated piece in the Hunter Leon line. All-white leather low-top with "
            "black trim, white HL monogram tonal-printed on the vamp. "
            "You either see it or you don't. US sizes 6–14."
        ),
        "price_display": "$139.00",
        "tags": ["white on white shoes", "tonal logo sneaker", "HL monogram shoe",
                 "luxury minimalist shoe", "hunter leon", "white leather sneaker",
                 "black trim white shoe", "mens premium sneaker"],
        "image_prompt": (
            "White leather low-top sneaker with black trim accent on sole and tongue. "
            "Tonal white HL monogram embossed on the lateral vamp panel — barely visible, intentionally subtle. "
            "Shot on pure white background with soft diffused lighting. Minimal luxury editorial."
        ),
        "mockup_prompt": (
            "3D mockup of all-white leather sneaker, black trim on edges, "
            "white-on-white HL monogram on the side panel (tone-on-tone). "
            "Both shoes at slight angle on white surface. Ultra clean, luxury minimal."
        ),
        "sales_copy": "White on white. Either you know or you don't. Hunter Leon HL.",
        "checkout_recommendation": "Etsy (popcustoms.com for POD manufacturing)",
        "manufacturer_url": "https://www.popcustoms.com",
        "manufacturer_notes": (
            "Popcustoms: select Men's Upgraded White Low Top Leather Sneakers. "
            "Upload white HL monogram PNG. Configure lateral panel for tonal placement. "
            "Base cost ~$30-35."
        ),
        "launch_checklist": [
            "Create Popcustoms account",
            "Export HL monogram as white PNG (2000px, transparent bg)",
            "Configure product at popcustoms.com",
            "Generate 3D mockups",
            "List on Etsy at $139",
            "Cross-link with Variant 3 in listing",
        ],
        "source_opportunity": "Hunter Leon Brand — Luxury Footwear",
        "status": "draft",
        "next_action": "Export HL monogram as white PNG and set up on Popcustoms",
    }

]


def get_created_products(session: Session) -> list[CreatedProduct]:
    return list(session.exec(select(CreatedProduct).order_by(CreatedProduct.created_at.desc())).all())


def create_product(session: Session, data: dict) -> CreatedProduct:
    pack = data.get("product_pack")
    product = CreatedProduct(
        name=data["name"],
        source_opportunity=data.get("source_opportunity"),
        platform=data.get("platform", "manual"),
        status=data.get("status", "draft"),
        url=data.get("url"),
        next_action=data.get("next_action"),
        price=data.get("price"),
        estimated_margin=data.get("estimated_margin"),
        product_pack=json.dumps(pack) if isinstance(pack, dict) else pack,
        design_variant=data.get("design_variant"),
        manufacturer=data.get("manufacturer"),
        notes=data.get("notes"),
    )
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


def seed_hunter_leon_products(session: Session) -> int:
    """Seed Hunter Leon shoe products if they don't exist yet."""
    seeded = 0
    for p in HUNTER_LEON_PRODUCTS:
        existing = session.exec(
            select(CreatedProduct).where(CreatedProduct.name == p["name"])
        ).first()
        if not existing:
            create_product(session, p)
            seeded += 1
    return seeded


def mark_launched(session: Session, product_id: int, url: str | None = None) -> CreatedProduct | None:
    product = session.get(CreatedProduct, product_id)
    if not product:
        return None
    product.status = "launched"
    product.launched_at = datetime.now(timezone.utc)
    if url:
        product.url = url
    product.updated_at = datetime.now(timezone.utc)
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


HERITAGE_SHIRT_PRODUCTS = [
    {
        "name": "Juneteenth 1865 Freedom Day Polo",
        "design_variant": "juneteenth_1865",
        "platform": "etsy",
        "manufacturer": "Printful",
        "price": 72.00,
        "estimated_margin": 0.56,
        "title": "Juneteenth 1865 Freedom Day Polo | Our History Our Power | Celebrate Freedom Shirt",
        "description": (
            "Juneteenth 1865. Freedom Day. Our History. Our Power. Celebrate Freedom. "
            "AOP polo with gold lettering, Pan-African stars, diagonal stripe, and breaking-chains "
            "freedom seal. Premium sublimation polo."
        ),
        "tags": ["juneteenth shirt", "juneteenth 1865", "freedom day outfit", "celebrate juneteenth",
                  "black liberation shirt", "june 19 shirt", "juneteenth polo", "juneteenth gift"],
        "sales_copy": "June 19, 1865. They couldn’t hold us then. They can’t hold us now.",
        "checkout_recommendation": "Etsy via Printful AOP polo",
        "manufacturer_url": "https://www.printful.com",
        "manufacturer_notes": "Printful AOP Men's Sublimation Polo. Upload full design PNG at 150 DPI. Base cost ~$30-32.",
        "launch_checklist": [
            "URGENT: List by May 20 — Juneteenth is June 19 (37 days)",
            "Upload to Printful AOP polo template",
            "Export mockups and create Etsy listing",
            "Run Etsy Ads $3/day — target 'juneteenth shirt'",
        ],
        "source_opportunity": "Heritage Threads — Juneteenth Collection",
        "status": "draft",
        "next_action": "⚨️ URGENT — Upload to Printful NOW. June 19 in 37 days.",
        "notes": "TIME-SENSITIVE: Must be live by May 20 to capture Juneteenth search traffic.",
    },
    {
        "name": "Royal Roots Polo — Kings Queens Legacy",
        "design_variant": "royal_roots",
        "platform": "etsy",
        "manufacturer": "Printful",
        "price": 75.00,
        "estimated_margin": 0.57,
        "title": "Royal Roots Polo | Kings Queens Legacy | Born From Greatness African Heritage Polo",
        "description": (
            "Royal Roots. Kings • Queens • Legacy. Born From Greatness. Gold crown, bold type, "
            "royal crest with two lions flanking Africa map shield. Kente patterns. AOP sublimation polo."
        ),
        "tags": ["royal roots shirt", "african royalty shirt", "black king polo",
                  "born from greatness shirt", "african lion shirt", "fathers day gift black man"],
        "sales_copy": "Kings. Queens. Legacy. Born from Greatness. Royal Roots.",
        "checkout_recommendation": "Etsy via Printful + Father's Day push",
        "manufacturer_url": "https://www.printful.com",
        "manufacturer_notes": "Printful AOP polo. Father's Day is June 15 — list by May 22.",
        "launch_checklist": [
            "List by May 22 — Father's Day June 15",
            "Upload to Printful AOP polo",
            "Create Etsy listing",
            "Add 'fathers day gift' to tags",
        ],
        "source_opportunity": "Heritage Threads — Royal Collection",
        "status": "draft",
        "next_action": "Upload to Printful. Father’s Day June 15 — list by May 22.",
        "notes": "Father's Day gift positioning. Highest price point in the line.",
    },
    {
        "name": "Black Excellence Polo — Strength Legacy Culture",
        "design_variant": "black_excellence",
        "platform": "etsy",
        "manufacturer": "Printful",
        "price": 69.00,
        "estimated_margin": 0.57,
        "title": "Black Excellence Polo | Strength Legacy Culture | African American Heritage Shirt",
        "description": (
            "Wear your legacy. Tree of Life emblem, bold gold lettering, Pan-African stripe, "
            "Kente geometric patterns. ‘Strength • Legacy • Culture. Rooted in Greatness.’ "
            "Premium AOP sublimation polo."
        ),
        "tags": ["black excellence shirt", "african american polo", "black pride clothing",
                  "pan african shirt", "tree of life shirt", "black history shirt", "kente print polo"],
        "sales_copy": "Strength. Legacy. Culture. Rooted in Greatness.",
        "checkout_recommendation": "Etsy via Printful — flagship evergreen design",
        "manufacturer_url": "https://www.printful.com",
        "manufacturer_notes": "Printful AOP polo. Evergreen best-seller. List by May 25.",
        "launch_checklist": [
            "Upload to Printful AOP polo template",
            "Create Etsy listing by May 25",
            "Evergreen — run ads year-round",
        ],
        "source_opportunity": "Heritage Threads — Black Excellence Collection",
        "status": "draft",
        "next_action": "Upload to Printful AOP polo and create Etsy listing.",
        "notes": "Flagship design. Evergreen demand year-round, peaks Black History Month + Juneteenth.",
    },
    {
        "name": "Melanin Magic Polo — Power Beauty Resilience",
        "design_variant": "melanin_magic",
        "platform": "etsy",
        "manufacturer": "Printful",
        "price": 65.00,
        "estimated_margin": 0.54,
        "title": "Melanin Magic Polo | Power Beauty Resilience | African Heritage AOP Polo",
        "description": (
            "Melanin Magic. Power, Beauty, Resilience — Made of History. "
            "Africa continent with sun halo, gold African mask medallion, Pan-African brushstrokes, "
            "kente border. Bold gold type on deep black. AOP sublimation polo."
        ),
        "tags": ["melanin magic shirt", "african american polo", "black queen shirt",
                  "melanin shirt", "africa pride polo", "pan african clothing", "black heritage shirt"],
        "sales_copy": "Power. Beauty. Resilience. Made of History. Melanin Magic.",
        "checkout_recommendation": "Etsy via Printful — strong gift item",
        "manufacturer_url": "https://www.printful.com",
        "manufacturer_notes": "Printful AOP polo. Strong gift buyer audience. List by May 27.",
        "launch_checklist": [
            "Upload to Printful AOP polo",
            "Create Etsy listing by May 27",
            "Target gift-buyer keywords",
        ],
        "source_opportunity": "Heritage Threads — Melanin Collection",
        "status": "draft",
        "next_action": "Upload to Printful and create Etsy listing.",
        "notes": "Strong gift appeal. Also good for Mother's Day next year.",
    },
]


def seed_heritage_shirts(session: Session) -> int:
    """Seed Heritage Polo shirt products into the created-products board."""
    seeded = 0
    for p in HERITAGE_SHIRT_PRODUCTS:
        existing = session.exec(
            select(CreatedProduct).where(CreatedProduct.name == p["name"])
        ).first()
        if not existing:
            create_product(session, p)
            seeded += 1
    return seeded


AMERICA_250_PRODUCTS = [
    {
        "name": "250 Years of Independence Polo",
        "design_variant": "250_years_independence",
        "platform": "etsy",
        "manufacturer": "Printful",
        "price": 79.00,
        "estimated_margin": 0.60,
        "title": "250 Years of Independence Polo | Black Legacy American Story | 1776 2026 Shirt",
        "description": (
            "250 Years of Independence. 1776–2026. Black Legacy. American Story. "
            "Distressed American flag across the chest, gold Liberty Bell medallion, "
            "African pattern cuffs. AOP sublimation polo. The shirt that tells the full story."
        ),
        "tags": ["250th anniversary shirt", "1776 2026 shirt", "black legacy shirt",
                 "america 250 polo", "independence day shirt", "july 4 2026 shirt",
                 "american heritage shirt", "liberty bell shirt", "black patriotic shirt",
                 "fathers day gift black man"],
        "sales_copy": "250 Years of Independence. Black Legacy. American Story. 1776–2026.",
        "manufacturer_url": "https://www.printful.com",
        "manufacturer_notes": "Printful AOP polo. Base cost ~$32. July 4 2026 = 52 days away.",
        "launch_checklist": [
            "⚨️ URGENT: July 4 2026 = 52 days. List by May 21.",
            "Also strong Father's Day gift (June 15 = 33 days)",
            "Upload to Printful AOP polo",
            "Etsy listing with '1776 2026' and 'america 250' tags",
            "Run Etsy Ads $3/day",
        ],
        "source_opportunity": "America 250th Anniversary Collection",
        "status": "draft",
        "next_action": "⚨️ URGENT — July 4 2026 in 52 days. Upload to Printful. List by May 21.",
        "notes": "TIME-SENSITIVE: America's 250th birthday July 4, 2026. Father's Day crossover.",
    },
    {
        "name": "250 Years Strong Polo",
        "design_variant": "250_years_strong",
        "platform": "etsy",
        "manufacturer": "Printful",
        "price": 79.00,
        "estimated_margin": 0.60,
        "title": "250 Years Strong Polo | Built by Many Defined by Legacy | 1776 2026 America Shirt",
        "description": (
            "250 Years Strong. Built by Many. Defined by Legacy. 1776–2026. "
            "American flag, Statue of Liberty medallion, 250 sleeve badge. "
            "Red, white, and gold AOP sublimation polo. Wear the milestone."
        ),
        "tags": ["250 years strong shirt", "america 250 polo", "1776 2026 shirt",
                 "july 4th 2026 shirt", "statue of liberty shirt", "patriotic polo shirt",
                 "built by many shirt", "american anniversary shirt", "mens polo patriotic"],
        "sales_copy": "250 Years Strong. Built by Many. Defined by Legacy.",
        "manufacturer_url": "https://www.printful.com",
        "manufacturer_notes": "Printful AOP polo. July 4 2026 = 52 days.",
        "launch_checklist": [
            "⚨️ URGENT: List by May 21 for July 4 indexing",
            "Upload to Printful AOP polo",
            "Use '250 years strong' as primary keyword",
        ],
        "source_opportunity": "America 250th Anniversary Collection",
        "status": "draft",
        "next_action": "⚨️ URGENT — Upload to Printful. July 4 in 52 days.",
        "notes": "TIME-SENSITIVE: America 250th anniversary. Broadest patriotic appeal in the collection.",
    },
    {
        "name": "America at 250 Polo — Freedom Faith Black Excellence",
        "design_variant": "america_at_250",
        "platform": "etsy",
        "manufacturer": "Printful",
        "price": 79.00,
        "estimated_margin": 0.60,
        "title": "America at 250 Polo | Freedom Faith and Black Excellence | 1776 2026 Heritage Shirt",
        "description": (
            "America at 250. Freedom, Faith, and Black Excellence. 1776–2026. "
            "Full flag upper chest, Africa map medallion, gold pinstripe body. "
            "The shirt that holds both stories at once. AOP sublimation polo."
        ),
        "tags": ["america at 250 shirt", "black excellence patriotic shirt", "1776 2026 polo",
                 "freedom faith shirt", "africa america shirt", "july 4 2026 shirt",
                 "black american heritage shirt", "america 250 black polo"],
        "sales_copy": "America at 250. Freedom, Faith, and Black Excellence. 1776–2026.",
        "manufacturer_url": "https://www.printful.com",
        "manufacturer_notes": "Printful AOP polo. Crossover appeal to both patriotic and Black heritage buyers.",
        "launch_checklist": [
            "⚨️ URGENT: List by May 21",
            "Best crossover design — targets both patriotic AND heritage search terms",
            "Upload to Printful AOP polo",
        ],
        "source_opportunity": "America 250th Anniversary Collection",
        "status": "draft",
        "next_action": "⚨️ URGENT — Best crossover design. Upload to Printful now.",
        "notes": "Highest crossover appeal. Targets patriotic AND Black heritage buyers. Strong Father's Day gift.",
    },
    {
        "name": "Woven Into the Nation Polo",
        "design_variant": "woven_into_nation",
        "platform": "etsy",
        "manufacturer": "Printful",
        "price": 75.00,
        "estimated_margin": 0.57,
        "title": "Woven Into the Nation Polo | 1776 2026 Our Hands Helped Build America | Black Heritage Shirt",
        "description": (
            "Woven Into the Nation. 1776–2026. Our Hands Helped Build America. "
            "Freedom. Legacy. Endurance. Distressed American flag, Black profile with sunburst halo "
            "inside chain-break seal, Southwestern border. The most powerful piece in the collection."
        ),
        "tags": ["woven into the nation shirt", "our hands built america shirt", "1776 2026 polo",
                 "black american history shirt", "freedom legacy endurance shirt",
                 "july 4 black heritage shirt", "black patriot shirt", "built america shirt"],
        "sales_copy": "Woven Into the Nation. Our Hands Helped Build America. 1776–2026.",
        "manufacturer_url": "https://www.printful.com",
        "manufacturer_notes": "Printful AOP polo. Most powerful messaging in the collection.",
        "launch_checklist": [
            "⚨️ URGENT: List by May 21",
            "Most emotionally resonant design — strong social sharing potential",
            "Upload to Printful AOP polo",
        ],
        "source_opportunity": "America 250th Anniversary Collection",
        "status": "draft",
        "next_action": "⚨️ URGENT — Upload to Printful. Most viral potential in the collection.",
        "notes": "Strongest emotional message. High social sharing potential. July 4 + Black heritage crossover.",
    },
]


def seed_america_250(session: Session) -> int:
    """Seed America 250th anniversary polo products."""
    seeded = 0
    for p in AMERICA_250_PRODUCTS:
        existing = session.exec(
            select(CreatedProduct).where(CreatedProduct.name == p["name"])
        ).first()
        if not existing:
            create_product(session, p)
            seeded += 1
    return seeded


def generate_product_pack(opportunity_title: str, opportunity_type: str = "digital") -> dict:
    """Generate a product pack skeleton from a Quick-Cash/Forge opportunity."""
    return {
        "title": opportunity_title,
        "platform": "gumroad" if opportunity_type == "digital" else "etsy",
        "price": None,
        "description": "",
        "tags": [],
        "image_prompt": f"Professional product image for: {opportunity_title}",
        "sales_copy": "",
        "launch_checklist": [
            "Create product/listing on chosen platform",
            "Add product images",
            "Set price and shipping (if physical)",
            "Publish and share link",
        ],
        "status": "draft",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
