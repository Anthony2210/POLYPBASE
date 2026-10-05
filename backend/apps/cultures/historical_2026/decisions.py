"""Explicit product and stakeholder decisions for the 2026 historical import.

Nothing here is inferred. Each entry is a reviewed decision applied only to the
exact source it names; there is no generic repair rule.
"""

SCHEMA = "polypbase.historical_import"
SCHEMA_VERSION = 1
GENERATOR_VERSION = "2026.1"

SOURCE_FILENAME = "Suivi_2026_actualisé.xlsx"
EXPECTED_WORKBOOK_SHA256 = (
    "14befa67441f289153576b5c53de462eaa05b4218df6d5439a44ee432f62cb67"
)

TARGET_ORGANIZATION_NAME = "Aquarium de Paris"
ISO_YEAR = 2026

# measured_on follows the established historical convention.
MEASURED_ON_CONVENTION = "iso_week_monday"
HISTORICAL_OBSERVER = None  # scientific observer unknown: BiologicalMeasurement.user = NULL

# Worksheet layout, identical on every visible sheet.
HEADER_ROW = 2
YEAR_ROW = 1
FIRST_WEEK_COLUMN = 6  # column F
RECAP_COLUMN = 5  # column E: 2025 recap, never part of the 2026 import
RECAP_YEAR = 2025
POLYP_LABEL = "Nb polypes"
EPHYRAE_LABEL = "Nb éphyrules"
EXPECTED_HEADER_LABELS = {1: "Espèce", 2: "N° boîte", 3: "Température (°C)"}

# Anthony: only these two exact identifiers are repaired.
IDENTIFIER_ALIASES = {
    "ASP-EVA1.01": "ASP-EVA-1.01",
    "CLA-JKA1.10": "CLA-JKA-1.10",
}

# Anthony: the populated column between W19 and W21 is W20. Reviewed column only.
REVIEWED_WEEK_HEADERS = {
    ("Semaestomeae", 25): {"raw": " -", "iso_week": 20},
}

# Anthony: in these exact source rows the labels are inverted. The row labelled
# "Nb éphyrules" holds polyps and the row labelled "Nb polypes" holds ephyrae.
ORIENTATION_CORRECTIONS = (
    {
        "sheet": "Rhizostomae",
        "source_box": "TTH-AVI-1.09",
        "polyp_row": 221,
        "ephyrae_row": 222,
    },
    {
        "sheet": "Rhizostomae",
        "source_box": "TTH-AVI-1.09",
        "polyp_row": 223,
        "ephyrae_row": 224,
    },
)

# Anthony: keep only the continuous 15 degC block of CCO-JKA-1.04. The isolated
# 10 degC block stays visible as excluded evidence; no TEST Box is created.
EXCLUDED_BLOCKS = (
    {
        "reason": "CCO_ISOLATED_10C_BLOCK",
        "sheet": "Semaestomeae",
        "source_box": "CCO-JKA-1.04",
        "polyp_row": 55,
        "ephyrae_row": 56,
        "block_temperature_c": 10,
        "kept_block_temperature_c": 15,
    },
)

# Visible label -> operational Species name, only where the label carries text
# that is not a taxon. Operational color/form qualifiers are otherwise retained.
SPECIES_LABEL_OVERRIDES = {
    "Bougainvillidae sp.\n(Jane Doe)": "Bougainvillidae sp.",
}

# Operational (non-binomial) references approved by Anthony. No binomial is invented.
APPROVED_OPERATIONAL_TAXA = (
    "Bougainvillidae sp.",
    "Hydrozoa sp.",
    "Tubularia sp.",
)

# Source label is known to differ from the Strain species and the Strain wins:
# Étienne keeps CLA for Cyanea lamarckii and gives Chrysaora lactea the code CLC,
# without rewriting historical identifiers.
STRAIN_LABEL_EXCEPTIONS = {
    "CLA-JKA-1": {
        "source_label": "Chrysaora lactea",
        "reason": "ETIENNE_D11_CLA_KEEPS_CYANEA_LAMARCKII",
    },
}

# Anthony / Étienne: one historical physical culture, one identity.
IDENTITY_CORRECTIONS = (
    {
        "from_code": "COR-JIS-1.001",
        "to_code": "ATO-JIS-1.001",
        "from_strain_code": "COR-JIS-1",
        "to_strain_code": "ATO-JIS-1",
        "to_species_name": "Atorella sp.",
        "to_species_code": "ATO",
        "reason": "ETIENNE_D21_COR_JIS_1_001_IS_ATO_JIS_1_001",
    },
)

# Anthony: explicit historical value correction, never a create-only row.
MEASUREMENT_CORRECTIONS = (
    {
        "box": "LDR-JAP-1.001",
        "iso_year": 2026,
        "iso_week": 18,
        "expected_current": {"polyps": 80, "ephyrae": 8},
        "target": {"polyps": 80, "ephyrae": 0},
        "reason": "ANTHONY_APPROVED_LDR_W18_80_8_TO_80_0",
    },
)

# Étienne HS list. Bounded, separate from measurement import.
HS_REASON = "Hors service (liste validée par Étienne)"
HS_BOXES = (
    "AFL-TAI-1.002",
    "AAL-FGU-1.002",
    "AVA-TAI-1.001",
    "CXA-JKA-1.008",
    "CMO-JEN-1.009",
    "CTA-PLI-1.001",
    "CTA-PLI-1.002",
    "CCE-ABE-1.008",
    "CAC-JKA-2.001",
    "CAC-JKA-2.002",
    "CAC-JKA-2.003",
    "CAC-JKA-2.004",
    "CHY-CPU-2.001",
    "COR-JIS-1.001",
    "CTU-CFC-2.007",
    "CLA-JKA-1.009",
    "EMA-FGU-1.001",
    "LAO-JKA-1.002",
    "LLU-JKA-2.002",
    "LMA-TAI-2.001",
    "LMA-TAI-2.002",
    "OCE-TAI-1.002",
    "PCA-CHO-1.005",
    "PPU-FRO-1.012",
    "PYU-CNI-1.001",
    "PYU-CNI-1.002",
    "RGO-FGU-1.002",
    "RGO-FGU-1.003",
    "RES-JKA-1.007",
    "SAR-NBE-2.001",
    "SAR-NBE-2.002",
    "SAR-NBE-2.003",
    "SAR-NBE-2.005",
    "TTH-AVI-1.009",
    "VAN-TCH-1.001",
)
HS_CODE_CORRECTIONS = {
    "COR-JIS-1.001": "ATO-JIS-1.001",
    "PCA-CHO-1.005": "PCA-JKA-1.005",
}
# Not approved: left for a human decision and never deactivated automatically.
HS_PENDING_CONFIRMATION = {
    "CTU-CFC-2.007": "CTU-FCF-2.007 is not approved as the same culture",
}
