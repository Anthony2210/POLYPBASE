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

# Recorded evidence, kept in the sealed manifest: Étienne keeps CLA for Cyanea
# lamarckii and gives Chrysaora lactea the code CLC, without rewriting historical
# identifiers. Whether the Chrysaora lactea Boxes CLA-JKA-1.009/.010 keep their
# codes is not decided, so the importer blocks instead of filing the source label
# under a Strain of another Species.
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
# Source evidence shared by the deactivation guard and read-only diagnostic.
# Extracted read-only from the SHA-256-checked V2 workbook (0d5cd16e...b160).
# Weeks follow worksheet headers, not column indices; zero is an observation.
# These facts block HS deactivation, never establish a new identity or status.
HS_V2_CONTRADICTIONS = {
    "CAC-JKA-2.004": {
        "literal": "CAC-JKA-2.04", "cell": "Semaestomeae!B13", "merged_range": "Semaestomeae!B13:B16",
        "blocks": ({"temperature_c": 15, "rows": "13-14", "first_week": 19, "last_week": 41},),
        "last_week": 41,
    },
    "COR-JIS-1.001": {
        "literal": "ATO-JIS-1.01", "cell": "Coronatae!B3", "merged_range": "Coronatae!B3:B4",
        "blocks": ({"temperature_c": 15, "rows": "3-4", "first_week": 1, "last_week": 41},),
        "last_week": 41,
    },
    "CLA-JKA-1.009": {
        "literal": "CLC-JKA-1.09", "cell": "Semaestomeae!B77", "merged_range": "Semaestomeae!B77:B80",
        "blocks": ({"temperature_c": 20, "rows": "77-78", "first_week": 1, "last_week": 40},),
        "last_week": 40,
    },
    "OCE-TAI-1.002": {
        "literal": "OCE-TAI-1.02", "cell": "Hydrozoa!B173", "merged_range": "Hydrozoa!B173:B174",
        "blocks": ({"temperature_c": 25, "rows": "173-174", "first_week": 1, "last_week": 40},),
        "last_week": 40,
    },
    "PCA-CHO-1.005": {
        "literal": "PCA-JKA-1.05", "cell": "Semaestomeae!B149", "merged_range": "Semaestomeae!B149:B152",
        "blocks": (
            {"temperature_c": 5, "rows": "149-150", "first_week": 1, "last_week": 2},
            {"temperature_c": 10, "rows": "151-152", "first_week": 3, "last_week": 40},
        ),
        "last_week": 40,
    },
}

# Not approved: left for a human decision and never deactivated automatically.
HS_PENDING_CONFIRMATION = {
    "CTU-CFC-2.007": "CTU-FCF-2.007 is not approved as the same culture",
}

# Identities whose correction needs a product decision. Read-only diagnostic input:
# nothing here authorizes a rename, a Strain change or a label. Source facts were
# extracted read-only from the SHA-256-checked workbooks named below.
CLEAN_HISTORY_SHA256 = "621d55e7b841b2261af533247e97a1969a79221d461104d147c8e0b74c95e1ee"
UNRESOLVED_IDENTITIES = {
    "D11_CLA_CLC": {
        "decision": "D11: CLA stays Cyanea lamarckii, Chrysaora lactea becomes CLC; "
                    "no historical identifier rewrite.",
        "open_question": "Do the Chrysaora lactea Boxes keep their CLA codes or become CLC, "
                         "and under which Strain?",
        "source_species": "Chrysaora lactea",
        "boxes": (
            {"code": "CLA-JKA-1.009", "candidate_code": "CLC-JKA-1.009",
             "original_literal": "CLA-JKA-1.09", "v2_literal": "CLC-JKA-1.09", "cell": "Semaestomeae!B77"},
            {"code": "CLA-JKA-1.010", "candidate_code": "CLC-JKA-1.010",
             "original_literal": "CLA-JKA1.10", "v2_literal": "CLC-JKA1.10", "cell": "Semaestomeae!B81"},
        ),
    },
    "D8_SME_SSP": {
        "decision": "D8: 2020-21 Stomolophus sp. Boxes carrying SME are SSP; affected 2022-23 "
                    "S. meleagris SME entries become Stomolophus sp.2 SSP; correct from 2024.",
        "open_question": "Which exact SME-JKA-1 Boxes are affected, do they keep their codes, "
                         "and are SSP-JKA-1 Boxes with the same number the same culture?",
        "namespace": "SME-JKA-1",
        "collision_namespace": "SSP-JKA-1",
        # Clean long-format history (CLEAN_HISTORY_SHA256): literal labels by year.
        "history": {
            "SME-JKA-1.01": {"2020": "Stomolophus meleagris", "2021": "Stomolophus sp.2"},
            "SME-JKA-1.04": {"2020": "Stomolophus meleagris", "2021": "Stomolophus sp.2",
                             "2022": "Stomolophus meleagris", "2023": "Stomolophus meleagris"},
            "SME-JKA-1.05": {"2021": "Stomolophus sp.2", "2022": "Stomolophus meleagris"},
            "SME-JKA-1.06": {"2022": "Stomolophus meleagris"},
            "SME-JKA-1.07": {"2022": "Stomolophus meleagris", "2023": "Stomolophus meleagris"},
            "SME-JKA-1.08": {"2023": "Stomolophus meleagris"},
            "SME-JKA-1.09": {"2023": "Stomolophus meleagris"},
            "SME-JKA-1.10": {"2023": "Stomolophus meleagris"},
            "SSP-JKA-1.08": {"2024": "Stomolophus sp.2"},
            "SSP-JKA-1.09": {"2024": "Stomolophus sp.2"},
            "SSP-JKA-1.10": {"2024": "Stomolophus sp.2"},
        },
        # Both 2026 workbooks agree: SME-JKA-2 and SSP-JKA-1.011/.012 are correct (2024+).
        "out_of_scope_namespaces": ("SME-JKA-2", "SME-ACH-1"),
    },
}
