"""Project configuration for LIX flash flood map builds."""

LIX_LA_PARISHES = {
    "ASCENSION", "ASSUMPTION", "EAST BATON ROUGE", "EAST FELICIANA",
    "IBERVILLE", "JEFFERSON", "LAFOURCHE", "LIVINGSTON", "ORLEANS",
    "PLAQUEMINES", "POINTE COUPEE", "ST. BERNARD", "ST. CHARLES",
    "ST. HELENA", "ST. JAMES", "ST. JOHN THE BAPTIST", "ST. TAMMANY",
    "TANGIPAHOA", "TERREBONNE", "WASHINGTON", "WEST BATON ROUGE",
    "WEST FELICIANA",
}

LIX_MS_COUNTIES = {
    "AMITE", "HANCOCK", "HARRISON", "JACKSON",
    "PEARL RIVER", "PIKE", "WALTHALL", "WILKINSON",
}

# Loose bbox around WFO LIX CWA and nearby runoff source area.
# xmin, ymin, xmax, ymax
LIX_BBOX = (-91.95, 28.65, -88.05, 31.35)

NCEI_STORMEVENTS_BASE_URL = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
