# AGENTS.md — Hostfact MCP Server

Instructies en kennis voor AI-systemen die werken met deze repository.

## Doel
Deze MCP server koppelt Hostfact (verkoop/facturatie) aan Claude.ai.
Primair gebruik: cross-checks tussen Pax8 (inkoop) en Hostfact (verkoop), en beheer van abonnementen.

---

## Hostfact API — Belangrijke valkuilen

### 1. Filteren op debiteur bij invoice/list
De Hostfact API accepteert `DebtorCode` **niet** als directe parameter bij `invoice/list`.
Gebruik altijd `searchat` + `searchfor`:

```
searchat=DebtorCode
searchfor=DB8595
```

Direct `DebtorCode=DB8595` meegeven wordt genegeerd — de API retourneert dan alle facturen.

### 2. Product → Service relatie
Een **product** in de Hostfact catalogus heeft een `ProductCode` (bijv. `MST-NCE-104-C100`).
Een **service** (abonnement) verwijst naar een product via diezelfde `ProductCode`.

**Als je een artikelnummer wil corrigeren op een service:**
→ Pas het product aan in de catalogus (`product/edit`), niet de service.
→ De service neemt de nieuwe productcode automatisch over.

De `edit_service` tool is bedoeld voor het aanpassen van **aantallen, prijs of periode** — niet voor het wijzigen van het artikelnummer.

### 3. Identifier vs. ProductCode bij edits
Voor `product/edit` en `service/edit` heeft de Hostfact API het interne numerieke `Identifier` nodig — niet de `ProductCode` of `DebtorCode`.

Werkwijze:
1. Doe eerst een `product/show` of `service/show` om het `Identifier` op te halen.
2. Gebruik dat `Identifier` in de edit-call.

### 4. Status codes facturen
| Code | Label |
|------|-------|
| 0 | Concept |
| 1 | Te betalen |
| 2 | Verstuurd |
| 3 | Betaald |
| 4 | Voldaan |
| 5 | Geblokkeerd |
| 6 | Aanmaning |
| 7 | Deurwaarder |
| 8 | Creditfactuur |
| 9 | Gecrediteerd |

Concept (0) en Gecrediteerd (9) worden uitgesloten bij omzetberekeningen.

### 5. Facturatieperiode codes
| Code | Betekenis |
|------|-----------|
| m | Maand |
| k | Kwartaal |
| j | Jaar |
| e | Eenmalig |

---

## Artikelnummer conventies (Esrocom)

| Prefix | Categorie |
|--------|-----------|
| `MST-NCE-*` | Microsoft 365 / Office 365 licenties (NCE) |
| `MST-3PI-*` | Derde partij software via Microsoft-kanaal |
| `AC-*` | QBIC Cloud server componenten |
| `QBIC-*` | QBIC eigen diensten (EDR, backup, security) |
| `voip-*` | VoIP abonnementen |
| `ict2.0-*` | ICT Beheer 2.0 abonnementen |
| `BG-*` | Glasvezel/internetverbindingen |

Microsoft 365 licenties horen **altijd** het prefix `MST-NCE-` te hebben. Andere prefixen (zoals `SW-Abo`, `O365-*`, lege codes) zijn verouderd of incorrect.

---

## Beschikbare tools

| Tool | Actie |
|------|-------|
| `list_debtors` | Lijst debiteuren |
| `get_debtor` | Debiteur detail |
| `get_debtor_summary` | Klantoverzicht incl. abonnementen + facturen (ondersteunt `year_filter`) |
| `list_invoices` | Facturen opvragen (filter via `debtor_code`, `date_from`, `status_filter`) |
| `get_invoice` | Factuur detail incl. regels en betaalhistorie |
| `list_creditinvoices` | Creditfacturen opvragen |
| `get_creditinvoice` | Creditfactuur detail |
| `list_services` | Actieve abonnementen (filter via `debtor_code`) |
| `get_service` | Abonnement detail via intern service-ID |
| `edit_service` | Abonnement aanpassen (aantal, prijs, periode) |
| `list_products` | Productcatalogus |
| `get_product` | Product detail |
| `edit_product` | Product aanpassen (incl. productcode wijzigen) |
| `add_debtor` | Nieuwe debiteur aanmaken |
| `add_service` | Abonnement toevoegen |
| `add_invoice` | Factuur aanmaken |
