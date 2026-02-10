# Observability Platform — Vendor Requirements

## Mandatory Requirements (Non-Negotiable)
1. **SOC 2 certification** — required by CISO (Marina Chen). Hard blocker if missing.
2. **99.9% SLA uptime** — production observability must meet this threshold
3. **Data encryption** — at rest and in transit
4. **RBAC** — role-based access control for dashboard and alert management

## Budget
- Approved annual budget: **$120K/year** (CONFIDENTIAL — do not share externally)
- Any vendor over budget requires CFO approval with business case

## Evaluation Criteria (Weighted)
1. Security & Compliance — 30% (SOC 2, encryption, audit logging)
2. Total Cost of Ownership — 25% (license + setup + support + engineering effort)
3. Technical Fit — 25% (integration with AWS stack, alerting, dashboarding)
4. Team Adoption — 20% (learning curve, existing expertise, ecosystem)

## Team Size
- 50 engineers across platform, product, and SRE teams
- 6 engineers on the platform team (primary users)

## Current Stack
- AWS (EKS, RDS, ElastiCache)
- Prometheus + custom dashboards (being replaced)
- PagerDuty for alerting

## Decision Process
- Alex Chen: recommendation owner
- David Park: final approver
- Marina Chen: security sign-off (SOC 2)
- Finance: budget approval
