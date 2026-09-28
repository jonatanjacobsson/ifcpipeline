"""
Default Lubekalk mapping for MagiCAD-for-Revit ventilation models (tracked).

``AssignLubekalkPset`` uses it unless ``mapping_module`` names another module in
this folder. For a project that differs, add ``<project>_lubekalk.py`` (gitignored)
that starts with ``from mappings.lubekalk_magicad import *`` and overrides only
what differs, e.g. ``MONTAGE_BY_SPACE`` for the project's room names.

Data only — the logic is in ``_lubekalk.Mapping``. Structure:

* codes     — PRODUCT_CODE, DUCT_CODE, BEND_CODES, BRANCH_CODES, *_REDUCTION_CODES,
              TRANSITION_CODE, SIMPLE_CODES, CLEANOUT_CODES, DETAIL_INSULATION_CODE,
              HANGER_CODES (from the Lubekalk import template's code lists)
* INSULATION        — BIP.InsulationType → fire class + Lubekalk insulation code
* MONTAGE_*         — height bands (Normtid VVS / SBUF 13833) and space-name rules
* MATERIAL_*        — Kanalmaterial from type descriptions
* REFERENCE_KINDS   — the CAD families' Pset_QuantityTakeOff.Reference → kind
* PRODUCT_*         — which elements are sakvaror, and the size in their name
* NOMINAL_DIAMETERS — the project's duct series (geometry-measured ends snap to it)

Codes, Montageläge and Kanalmaterial strings are Lubekalk's own (its import
template's lists, character for character). The rest was fitted to a MagiCAD for
Revit / Lindab model and validated on one (11 665 elements, 100 % resolved). Every
entry marked ANTAGANDE is a judgement a kalkylator should confirm.
"""

INF = float("inf")

# --- Codes ------------------------------------------------------------------

PRODUCT_CODE = "SAK"                      # Alla sakvaror
DUCT_CODE = {"round": "CK", "rect": "RK"}  # Kanal / Plåtyta G1 300

#: Bend angle (°) → code; first limit that the angle does not exceed wins.
BEND_CODES = {
    "round": [(37.5, "CB3"), (67.5, "CB4"), (INF, "CB9")],  # Böj 30 / 45 / 90
    "rect": [(INF, "RB")],                                   # Böj
}

#: (kind, run shape, branch shape) → code.
BRANCH_CODES = {
    ("tee", "round", "round"): "CT",      # T-rör
    ("tee", "round", "rect"): "CAR",      # Avstick m. rekt
    ("tee", "rect", "rect"): "RA1",       # Avstick u.radie  — ANTAGANDE (LTTR)
    ("tee", "rect", "round"): "RA2",      # Avstick m.radie
    ("cross", "round", "round"): "CX",    # X-rör
    ("cross", "rect", "rect"): "RA1",     # ANTAGANDE
    ("cross", "round", "rect"): "CAR",    # X-rör med ett rektangulärt avstick — ANTAGANDE
    ("tap", "round", "round"): "CA",      # Avstick
    ("tap", "round", "rect"): "CAR",      # Avstick m. rekt
    ("tap", "rect", "round"): "RA2",      # Avstick m.radie
    ("tap", "rect", "rect"): "RA1",
}

#: Circular reduction: in a pipe run vs. directly on a fitting.
ROUND_REDUCTION_CODES = {False: "CDR", True: "CDD"}   # Dim i rör / Dim på detalj
#: Rectangular: one or both side dimensions change.
RECT_REDUCTION_CODES = {1: "RD1", 2: "RD2"}           # Dimändr. 1 mått / 2 mått

TRANSITION_CODE = "RÖC"  # Överg. centrisk (rektangulär ↔ cirkulär) — ANTAGANDE för LORU

#: (kind, shape, connected to a fitting rather than a pipe) → code.
SIMPLE_CODES = {
    ("cap", "round", False): "CGR", ("cap", "round", True): "CGD",   # Gavel i rör / på detalj
    ("cap", "rect", False): "RG", ("cap", "rect", True): "RG",       # Gavel
    ("joint", "round", False): "CIS", ("joint", "round", True): "CIS",  # Iskjut — ANTAGANDE
    ("joint", "rect", False): "RPB", ("joint", "rect", True): "RPB",    # Passbit — ANTAGANDE
}
#: Cleaning cover: on fire-insulated duct → A60, otherwise A15. ANTAGANDE.
CLEANOUT_CODES = {True: "CR1", False: "CR2"}   # Renslucka A60 / A15

DETAIL_INSULATION_CODE = "RID"   # Detaljtillägg isol (per isolerad fitting)

#: Provisions for voids (Pset_ProvisionForVoid): Håltagning. Whether hole-making is in the
#: ventilation contract (vs. cast-in by the builder) is the kalkylator's call — ANTAGANDE.
VOID_CODES = {"round": "CH", "rect": "RH"}

#: Hangers per metre by fire class of the duct's insulation.
HANGER_CODES = {
    "round": {None: "CU1", "15": "CU2", "30": "CU3", "60": "CU4", "120": "CU5"},
    "rect": {None: "RU1", "15": "RU2", "30": "RU3", "60": "RU4", "120": "RU5"},
}
#: No per-metre hangers where the duct is not hung.
NO_HANGER_MONTAGE = ("Schakt", "I mark", "Ingjutning i vägg", "Ingjutning i valv")

# --- Insulation (BIP.InsulationType on the host) ------------------------------
#: fire_class drives hangers; "round"/"rect" are Lubekalk insulation codes.
#: The template lists insulation codes only for rectangular ducts (RI*), and none
#: for plain thermal insulation — those stay None and are reported as
#: "isolering att koda" with their lengths instead of being guessed.
INSULATION = {
    "V20": {"fire_class": None, "round": None, "rect": None},   # Utv. lamellmatta Al-folie 20 mm, värme
    "V30": {"fire_class": None, "round": None, "rect": None},
    "V50": {"fire_class": None, "round": None, "rect": None},
    "B30": {"fire_class": "30", "round": None, "rect": "RI7"},  # Brand 30 mm (på kanaler) → A30 — ANTAGANDE
    "B40": {"fire_class": "30", "round": None, "rect": "RI7"},  # Brand 40 mm → A30 — ANTAGANDE
    "B50": {"fire_class": "30", "round": None, "rect": "RI7"},  # Brand 50 mm → A30 — ANTAGANDE
    "B80": {"fire_class": "30", "round": None, "rect": "RI7"},  # Brand stenull 80 mm → A30 — ANTAGANDE
    "B100": {"fire_class": "60", "round": None, "rect": "RI8"}, # Brand stenull 100 mm → A60 — ANTAGANDE
    "K80": {"fire_class": "30", "round": None, "rect": "RI7"},  # Kond./brand stenull 80 mm → A30 — ANTAGANDE
}

# --- Montageläge ------------------------------------------------------------
#: Exact strings from the Lubekalk list (keep the padding).
MONTAGE_BANDS = [
    (4.0, "Normalmontage      th. <4.0 m"),
    (6.0, "Höjdmontage 6 m   th. <6.0 m"),
    (8.0, "Höjdmontage 8 m   th. <8.0 m"),
    (INF, "Höjdmontage spec. th. >8.0 m"),
]
#: Space name decides before height. First match wins.
MONTAGE_BY_SPACE = [
    (r"kryp|crawl", "Kryputrymme         th. <1.8 m"),
    (r"AHU plant room|fläktrum|fan room", "Fläktrum"),
    (r"riser|schakt|shaft", "Schakt"),
    (r"roof top", "På yttertak"),
]
SPACE_PROPERTIES = ("BIP.SpaceLongName",)

# --- Kanalmaterial ------------------------------------------------------------
MATERIAL_PROPERTIES = ("BIP.TypeDescription", "BIP.ProductType")
MATERIAL_RULES = [
    (r"rostfri|stainless", "Rostfritt"),
    (r"aluzink", "Aluzink"),
    (r"magnelis", "Magnelis"),
    (r"epoxi", "Epoxibehandlad"),
    (r"lackad", "Lackade"),
    (r"\bplast|pvc|\bPP\b", "Plast"),
]
MATERIAL_DEFAULT = "Galvplåt"

# --- Model vocabulary (Pset_QuantityTakeOff.Reference) -------------------------
#: reference → (kind, fixed code or None, confidence)
REFERENCE_KINDS = {
    # ducts
    "Cirkulär kanal": ("duct", None, "hög"),
    "Lindab SR": ("duct", None, "hög"),
    "Rektangulär kanal": ("duct", None, "hög"),
    "Rektangulär rostfri stål kanal": ("duct", None, "hög"),
    # bends
    "magicirc_elbow_full_radius_001": ("bend", None, "hög"),
    "magicirc_elbow_segmented_001": ("bend", None, "hög"),
    "BU": ("bend", None, "hög"),
    "LBXR": ("bend", None, "hög"),
    "LBR (LBXR)": ("bend", None, "hög"),
    "magirect_elbow1_001": ("bend", None, "hög"),
    # branches
    "magicirc_tee_centric_90_001": ("tee", None, "hög"),
    "TVU45": ("tee", None, "medel"),  # "Small Roundings", 3 portar — ANTAGANDE: T-rör
    "magicirc_cross_centric_90_001": ("cross", None, "hög"),
    "magicirc_tapoutlet_001": ("tap", None, "hög"),
    "magirect_outlet1_001 from cirk lika Lindab LPSR": ("tap", "CAR", "hög"),
    "ILRU circ/rect tap radius": ("tap", "RA2", "hög"),
    "magirect_tbranch1_001 T-stycke lika Lindab LTTR": ("tee", None, "medel"),
    # size changes
    "RCU": ("reduction", None, "hög"),
    "RCLU": ("reduction", None, "hög"),
    "RLU": ("reduction", None, "hög"),
    "magicirc_reduction_001": ("reduction", None, "hög"),
    "LDR": ("reduction", None, "hög"),
    "magirect_reduction1_001": ("reduction", None, "hög"),
    "LORU": ("transition", None, "medel"),
    "magicirc_multi_shape_trans_001": ("cap", "RG", "låg"),  # Rekt. ändlock med cirk. påstick — ANTAGANDE
    # ends, joints, access
    "magicirc_plug_001": ("cap", None, "hög"),
    "magirect_plug1_001": ("cap", None, "hög"),
    "LEPR": ("cap", None, "hög"),
    "magicirc_joint_001": ("joint", None, "medel"),
    "magirect_joint1_001": ("joint", None, "medel"),
    "EPFH": ("cleanout", None, "medel"),
}

#: Products (sakvaror) by designation; checked before REFERENCE_KINDS.
PRODUCT_PATTERNS = [
    r"^FTCU", r"^PRA/N", r"^IRIS", r"^SLGPU", r"^CLA-B", r"^FTMU", r"^TUBUS", r"^KGEB", r"^SAL_",
    # Roof hood (combined exhaust cowl + inlet). Exported as a terminal in one Revit export and as a
    # one-port IfcFlowFitting in the next (Nobel M1 v43 → v46), where it had no size and fell to Saknas.
    r"^BRNF-",
]
#: The connection diameter a product states in its designation.
CONNECTION_SIZE_PATTERNS = [
    r"^FTCU-(\d{3})", r"^PRA/N-(\d{3})", r"^IRIS-(\d{3})", r"^SLGPU (\d{3})", r"^CLA-B[- ](\d{3})",
    r"^FTMU (\d{3})", r"RAW-(\d{3})", r"^KGEB-(\d{3})", r"^prio (\d{3})",
    # Generic "<CODE>-<diameter>" designations: BSKC6-200, PVAP-250-600-50, JET-200, KTI-200, RAEC-400, AXC-500.
    # Used only for ports the duct network left unsized, on in-line products (≤ 2 ports).
    r"^[A-Z][A-Za-z0-9]*-(\d{3})\b",
]
#: Nominal circular duct diameters (Swedish series; on the validation model every
#: BIP.Diameter but one stray 219 mm). Geometry-measured ends snap to these.
NOMINAL_DIAMETERS = (80, 100, 125, 160, 200, 250, 315, 355, 400, 500, 630, 800, 1000, 1250)
PRODUCT_NAME_PROPERTIES = ("BIP.ProductType", "Pset_QuantityTakeOff.Reference")

#: Whole classes that are sakvaror.
PRODUCT_CLASSES = (
    "IfcFlowTerminal", "IfcFlowController", "IfcFlowTreatmentDevice", "IfcFlowMovingDevice",
    "IfcEnergyConversionDevice", "IfcDiscreteAccessory", "IfcDistributionControlElement",
    "IfcFlowStorageDevice", "IfcBuildingElementProxy",
)
#: Building-element parts that are the host's insulation (priced through the host).
INCLUDED_PART_PATTERNS = [r"Duct Insulation", r"^\(?[VBK]\d{2,3}\)?\b"]

#: Not ventilation calculation scope, with the reason recorded on the element.
OUT_OF_SCOPE_CLASSES = {
    "IfcCovering": "Byggdel (undertak/beklädnad) i ventilationsmodellen — ej ventilationskalkyl",
    "IfcWall": "Byggdel i ventilationsmodellen — ej ventilationskalkyl",
    "IfcFurnishingElement": "Inredning — ej ventilationskalkyl",
    "IfcOpeningElement": "Öppning",
    "IfcVirtualElement": "Virtuellt element",
}
