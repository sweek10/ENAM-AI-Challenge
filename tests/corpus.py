"""Hand-written test headlines in BSE/NSE style, with expected category.

These are TEST DATA, not real filings. `None` in the rule column means the rules
are expected to abstain (genuinely ambiguous -> page-1 rules or "unreviewed").
Columns: (exchange_category, subcategory, headline, expected_category, rule_should_catch)
"""

CORPUS = [
    # results family
    ("Result", "Financial Results", "Unaudited Financial Results for the quarter ended September 30, 2026", "results", True),
    ("Board Meeting", "Outcome of Board Meeting", "Outcome of Board Meeting - Approval of Unaudited Standalone and Consolidated Financial Results for Q2 FY27", "results", True),
    ("Board Meeting", "Board Meeting Intimation", "Board Meeting Intimation for Considering Financial Results for the quarter ending 30th September 2026", "results", True),
    ("Company Update", "Newspaper Publication", "Copy of Newspaper Publication of Financial Results for Q2 FY27", "results", True),
    ("Company Update", "Investor Presentation", "Investor Presentation on Q2 FY27 performance", "results", True),
    # earnings call / investor meets
    ("Company Update", "Earnings Call Transcript", "Transcript of Earnings Call held on November 10, 2026", "earnings_call", True),
    ("Company Update", "Analyst / Investor Meet", "Intimation of Earnings Conference Call for Q2 FY27", "earnings_call", True),
    ("Company Update", "Analyst / Investor Meet", "Audio Recording of the Earnings Call", "earnings_call", True),
    ("Company Update", "Analyst / Investor Meet", "Schedule of Analyst / Institutional Investor Meetings", "investor_meetings", True),
    ("Company Update", "Analyst / Investor Meet", "Participation in the Motilal Oswal Annual Global Investor Conference", "investor_meetings", True),
    # management change
    ("Company Update", "Change in Management", "Resignation of Mr. A. Sharma as Chief Financial Officer", "management_change", True),
    ("Company Update", "Change in Directorate", "Appointment of Ms. R. Iyer as Managing Director and CEO", "management_change", True),
    ("Company Update", "Change in Directorate", "Appointment of Independent Director", "management_change", True),
    ("Company Update", "Change in Management", "Cessation of Company Secretary and Compliance Officer", "management_change", True),
    # M&A
    ("Company Update", "Acquisition", "Acquisition of 100% stake in XYZ Technologies Pvt Ltd", "M&A", True),
    ("Company Update", "Scheme of Arrangement", "Scheme of Amalgamation of wholly owned subsidiaries with the Company", "M&A", True),
    ("Company Update", "Joint Venture", "Execution of Joint Venture Agreement with ABC Corp", "M&A", True),
    ("Company Update", "Others", "Incorporation of a wholly owned subsidiary in Singapore", "M&A", True),
    # fund raise
    ("Company Update", "Allotment of Securities", "Allotment of Non-Convertible Debentures on private placement basis", "fund_raise", True),
    ("Board Meeting", "Outcome of Board Meeting", "Board approves Buyback of equity shares", "fund_raise", True),
    ("Company Update", "Others", "Issue of Commercial Paper", "fund_raise", True),
    ("Company Update", "Others", "Launch of Qualified Institutions Placement (QIP)", "fund_raise", True),
    # litigation
    ("Company Update", "Litigation", "Order passed by the Commissioner of GST imposing penalty", "litigation", True),
    ("Company Update", "Others", "Receipt of Warning Letter from USFDA for Halol facility", "litigation", True),
    ("Company Update", "Others", "Update on the matter pending before Hon'ble Supreme Court", "litigation", True),
    ("Company Update", "Others", "Admission of application under Section 9 of IBC by NCLT", "litigation", True),
    # insider / pledging
    ("Insider Trading / SAST", "Reg. 7(2)", "Disclosure under Regulation 7(2) of SEBI (Prohibition of Insider Trading) Regulations", "insider_trading", True),
    ("Insider Trading / SAST", "Reg. 29(2)", "Disclosure under Regulation 29(2) of SEBI SAST Regulations - Promoter acquisition", "insider_trading", True),
    ("Insider Trading / SAST", "Reg. 31(1)", "Disclosure of creation of pledge on shares by promoter", "pledging", True),
    ("Insider Trading / SAST", "Reg. 31(1)", "Disclosure of release of encumbrance by promoter group", "pledging", True),
    # credit rating
    ("Company Update", "Credit Rating", "CRISIL reaffirms credit rating of the Company", "credit_rating", True),
    ("Company Update", "Credit Rating", "ICRA downgrades long term rating to A+ with negative outlook", "credit_rating", True),
    # dividend / bonus split
    ("Corp. Action", "Dividend", "Record date for Interim Dividend", "dividend", True),
    ("Corp. Action", "Bonus", "Board recommends Bonus issue of equity shares in ratio 1:1", "bonus_split", True),
    ("Corp. Action", "Split", "Sub-division of equity shares from face value Rs 10 to Rs 2", "bonus_split", True),
    # capex
    ("Company Update", "Others", "Commencement of commercial production at new plant in Gujarat", "capex", True),
    ("Board Meeting", "Outcome of Board Meeting", "Board approves capacity expansion of 2 MTPA at Odisha", "capex", True),
    # press releases
    ("Company Update", "Press Release / Media Release", "Press Release - Company bags order worth Rs 1,200 crore", "press_release", True),
    ("Company Update", "Award of Order", "Receipt of order from Indian Railways", "press_release", True),
    ("Company Update", "Clarification", "Clarification on news item appearing in media", "press_release", True),
    ("Company Update", "Others", "Launch of new product range in consumer electronics", "press_release", True),
    # meetings
    ("AGM/EGM", "Postal Ballot", "Notice of Postal Ballot", "postal_ballot", True),
    ("AGM/EGM", "Postal Ballot", "Voting results of Postal Ballot along with Scrutinizer's Report", "postal_ballot", True),
    ("AGM/EGM", "AGM", "Notice of 49th Annual General Meeting", "agm_egm", True),
    # routine paperwork
    ("Company Update", "Trading Window", "Closure of Trading Window", "unclassified", True),
    ("Company Update", "Compliance", "Certificate under Regulation 74(5) of SEBI (Depositories and Participants) Regulations, 2018", "unclassified", True),
    ("Company Update", "Allotment of Securities", "Allotment of equity shares under ESOS 2018", "unclassified", True),
    ("Company Update", "Others", "Loss of Share Certificates", "unclassified", True),
    ("Company Update", "Others", "Business Responsibility and Sustainability Report", "unclassified", True),
    ("Company Update", "Compliance", "Appointment of Secretarial Auditor", "unclassified", True),
    ("Company Update", "Newspaper Publication", "Copy of Newspaper Advertisement - Notice to shareholders on IEPF transfer", "unclassified", True),
    # genuinely ambiguous -> headline rules should abstain
    ("Board Meeting", "Outcome of Board Meeting", "Outcome of Board Meeting held on November 12, 2026", "unknown", False),
    ("Company Update", "General Updates", "Disclosure under Regulation 30 of SEBI (LODR) Regulations, 2015", "unknown", False),
    ("Company Update", "Updates", "Intimation pursuant to Regulation 30", "unknown", False),
    ("Company Update", "Allotment of Securities", "Allotment of equity shares", "unknown", False),
    ("Company Update", "Others", "Update on ongoing matter", "unknown", False),
    ("Company Update", "Others", "Intimation of Book Closure", "unknown", False),
    ("Company Update", "Others", "Shareholding Pattern for the quarter", "unknown", False),
]
