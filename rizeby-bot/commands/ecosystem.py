"""
Commands: /ecosystem, /canton, /cantonboard, /rwa, /vision87, /vision60, /kairos
Bonus: /name or /entity triggers auto-lookup.
Exact data from rize-ecosystem hub.
"""
from utils.github_data import get_entities
from utils.fuzzy import find_entity

TRIZE_ECOSYSTEM = [{"id": "canton", "name": "Canton Network", "tag": "BLOCKCHAIN", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/canton-DxoXFUH1.png", "text": "The name 'Canton' comes from Switzerland — a federated nation where each city runs its own rules but interoperates under one country. This is exactly how Canton Network works: every application has its own governance, its own privacy controls, down to the sub-transaction level, while maintaining full native composability with every other application on the network. Most blockchains force a trade-off — build public and expose everything, or build private and lose interoperability. Canton removes that trade-off entirely. Launched in May 2023 by a consortium including Goldman Sachs, BNP Paribas, Deutsche Börse, Cboe, and Digital Asset, Canton processes over $2 trillion in monthly transactions and safeguards $4+ trillion in digital assets. T-RIZE operates as a Premier Member, Validator, and RWA issuer on Canton, leveraging its privacy-enabled rails for compliant issuance, registry, and post-trade lifecycle management of tokenized real-world assets."}, {"id": "particula", "name": "Particula", "tag": "SERVICES", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/particula-CblFVUrc.png", "text": "Particula is an independent digital asset risk rating agency — the first to issue a pre-issuance risk rating on the Canton Network, assigning a B+ rating to T-RIZE's KAI18-1 tokenized instrument. Particula provides real-time risk monitoring, credit analysis, and due diligence infrastructure for tokenized assets. In a market where institutional investors require rigorous risk frameworks before deploying capital on-chain, Particula fills a critical gap by bringing traditional structured finance rating methodologies to the blockchain. Their independence and methodology have made them a cornerstone of institutional credibility in the RWA space."}, {"id": "chainlink", "name": "Chainlink", "tag": "ORACLE", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/chainlink-CuysSQmw.png", "text": "Chainlink is the world's leading decentralized oracle network, securing over $75 billion in smart contract value across 1,800+ integrations. For T-RIZE, Chainlink provides the oracle infrastructure that acts as an external data integrity layer — reducing data manipulation risk by introducing an independent verification source via Merkle root. Chainlink's CCIP enables T-RIZE to connect tokenized assets across multiple blockchain ecosystems including Avalanche and Base, while Proof of Reserve capabilities ensure transparent, verifiable backing for digital assets."}, {"id": "digitalasset", "name": "Digital Asset", "tag": "INFRASTRUCTURE", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/digitalAsset-DXdPSw_s.png", "text": "Digital Asset is the technology company behind DAML (Digital Asset Modeling Language) — the smart contract language that powers the Canton Network. Co-founded by Yuval Rooz and Blythe Masters (former JPMorgan head of global commodities), Digital Asset has raised $300M+ from investors including Goldman Sachs, Citigroup, JPMorgan, and Deutsche Börse. DAML is the programming language used by T-RIZE to deploy its tokenization applications on Canton, making Digital Asset the foundational technology layer beneath every T-RIZE structured product."}, {"id": "fireblocks", "name": "Fireblocks", "tag": "CUSTODIAN", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/fireblocks-BizZbcST.png", "text": "Fireblocks is the leading enterprise digital asset security platform, trusted by over 1,800 financial institutions including BNY Mellon, BNP Paribas, and Goldman Sachs to secure $4 trillion+ in digital asset transfers. Fireblocks supports a wide range of custody architectures and compositions for institutional deployments. As an example of one possible architecture, in the Kairos Digital Loan Notes (KAI18-1) deal, T-RIZE deliberately separated the Fireblocks MPC signature environment from the Canton node — a security design choice highlighted in Particula's B+ rating report, ensuring no single point of compromise for that specific instrument."}, {"id": "republic", "name": "Republic", "tag": "INVESTMENT", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/republic-DwRcX6x0.png", "text": "Republic is one of the world's leading regulated investment platforms with over 3 million users, $3 billion in deployed capital, and more than 2,500 ventures backed across 150+ countries. Backed by Valor Equity Partners, Galaxy Interactive, and HOF Capital, Republic brings unparalleled retail and accredited investor distribution reach to T-RIZE's tokenized real estate offerings. T-RIZE partnered with Republic to distribute Vision 60 — the first new-construction real estate equity tokenization natively issued on Canton Network."}, {"id": "texture", "name": "Texture Capital", "tag": "INVESTMENT", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/texture-CdcnIvxs.png", "text": "Texture Capital is a SEC-registered broker-dealer and FINRA member that serves as the primary US distribution infrastructure for T-RIZE's tokenized offerings, including Vision 87. As a registered Alternative Trading System (ATS), Texture Capital enables compliant primary issuance and secondary market trading for security tokens — one of the few regulated platforms in the US capable of handling Reg D 506(c) digital securities offerings."}, {"id": "base", "name": "Base", "tag": "BLOCKCHAIN", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/base-DzeCCRUV.png", "text": "Base is a secure, low-cost, developer-friendly Ethereum L2 incubated by Coinbase, one of the world's largest crypto exchanges. T-RIZE uses Base as one of the EVM-compatible deployment networks for its tokenized real estate offerings including Vision 60. Base provides low-cost execution, robust security, and seamless integration with Ethereum and capital markets ecosystems — offering a strong foundation for tokenized financial products alongside Avalanche and Canton Network."}, {"id": "cantonfoundation", "name": "Canton Foundation", "tag": "FOUNDATION", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/cantonFoundation-Cs_Kzuz4.png", "text": "The Canton Foundation is the governing body of the Canton Network, responsible for the network's strategic direction, governance framework, and ecosystem development. Madani Boukalba (T-RIZE CEO) was announced as a board member of the Canton Foundation in December 2025, elevating T-RIZE to a premier position within the network's governance structure. The Foundation oversees the world's most significant institutional blockchain infrastructure, with participation from Goldman Sachs, BNP Paribas, DTCC, Euroclear, and BNY Mellon."}, {"id": "kaiko", "name": "Kaiko", "tag": "SERVICES", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/kaiko-D9u99Orf.png", "text": "Kaiko is the global independent leader in digital asset market data, analytics, crypto indices, and pricing for institutional investors. With coverage of 120,000+ instruments across 100+ exchanges and trusted by the world's leading financial institutions, Kaiko provides the institutional-grade market data infrastructure that underpins price discovery, risk management, and compliance for tokenized asset markets. Their data feeds are used by central banks, asset managers, and regulators globally."}, {"id": "dfns", "name": "DFNS", "tag": "CUSTODIAN", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/dfns-xlbSi2Fj.png", "text": "DFNS is a keyless crypto custody infrastructure provider offering MPC-based wallet-as-a-service for institutions. DFNS enables financial institutions to manage digital asset custody without private key exposure — a critical requirement for regulated entities handling tokenized securities. Their infrastructure supports T-RIZE's institutional-grade custody requirements, enabling seamless integration with existing financial workflows while maintaining the security standards demanded by global regulators."}, {"id": "aerodrome", "name": "Aerodrome", "tag": "DEX", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/aerodrome-CWA_aXvm.png", "text": "Aerodrome is a leading decentralized exchange built on Base, designed to serve as the primary liquidity hub for the Base ecosystem. Through its DeFi rails, Aerodrome provides RIZE token holders on-chain access to decentralized trading — making RIZE accessible in DeFi. Aerodrome is then a long-term DeFi infrastructure supporting RIZE accessibility and deployment."}, {"id": "arrakis", "name": "Arrakis", "tag": "LIQUIDITY", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/arrakis-DYsZCI7p.png", "text": "Arrakis provides automated liquidity management and market making on decentralized exchanges, with a focus on Aerodrome on Base. For RIZE, Arrakis actively manages liquidity positions to ensure efficient, tight-spread trading on-chain — maintaining the depth and consistency that traders and investors require for smooth entry and exit into RIZE positions in DeFi."}, {"id": "bitpanda", "name": "Bitpanda", "tag": "EXCHANGE", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/bitpanda-BmVJTn2R.png", "text": "Bitpanda is Europe's leading digital investment platform, regulated across multiple European jurisdictions and serving 6+ million customers. Headquartered in Vienna, Bitpanda provides retail and institutional investors with access to cryptocurrencies, stocks, ETFs, and metals. Bitpanda lists the RIZE token, providing European retail investors with a regulated, compliant access point to RIZE trading — making it the EU-focused counterpart to Kraken and MEXC in the RIZE exchange ecosystem."}, {"id": "kraken", "name": "Kraken", "tag": "EXCHANGE", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/kraken-Bk9Og8uZ.png", "text": "Kraken is one of the world's oldest and most trusted cryptocurrency exchanges, founded in 2011 and serving institutional and retail clients across 190+ countries. Kraken Pro handles institutional crypto trading with deep liquidity across hundreds of trading pairs. As a T-RIZE ecosystem partner and the primary exchange where RIZE token is listed with USD pairs, Kraken provides the regulated exchange infrastructure and liquidity access that institutional investors require for digital asset exposure."}, {"id": "mexc", "name": "MEXC", "tag": "EXCHANGE", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/MEXC-Dba-BYSi.png", "text": "MEXC is a leading global centralized cryptocurrency exchange with $2B+ in daily trading volume, serving 10+ million users across 200+ countries. MEXC provides deep liquidity for the RIZE token and serves as a key access point for Asian and emerging market investors seeking exposure to the T-RIZE ecosystem. Their broad geographic reach complements Kraken's institutional focus, together providing RIZE with global exchange coverage across diverse investor profiles."}, {"id": "revolut", "name": "Revolut", "tag": "EXCHANGE", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/revolut-DUlyhaUO.png", "text": "Revolut is a global financial super-app with 45+ million customers across 38 countries, offering banking, payments, and cryptocurrency trading. With $33B+ valuation and a European banking license, Revolut represents one of the most significant retail fintech platforms globally. RIZE is already listed on Revolut and Revolut X, giving tens of millions of mainstream financial consumers direct access to the RIZE token through one of the world's most widely used financial apps."}, {"id": "erc3643", "name": "ERC3643", "tag": "STANDARD", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/erc3643-BucHG6vK.png", "text": "ERC3643 (T-REX) is the leading token standard for compliant security tokens on EVM-compatible blockchains, developed by Tokeny Solutions. In simple terms, it adds a compliance layer directly into the token itself — meaning that before any transfer happens, the token automatically checks whether the recipient is authorized to hold it (KYC verified, accredited investor, correct jurisdiction). Think of it as a smart contract that acts as both the asset and its own compliance officer. This makes it impossible to accidentally transfer a regulated security to an ineligible investor — a fundamental requirement for institutional adoption. T-RIZE's tokenization architecture is built around ERC3643, ensuring every tokenized asset maintains full regulatory integrity across jurisdictions."}, {"id": "ets", "name": "ÉTS", "tag": "ACADEMIC", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/ets-BfEhYmAW.png", "text": "École de Technologie Supérieure (ÉTS) is one of Canada's leading engineering and technology universities, part of the Université du Québec network. T-RIZE co-leads the first Industrial Research Chair in Tokenization & Federated Learning at ÉTS, supported by the Government of Canada. T-RIZE Lab at ÉTS develops privacy-preserving decentralized machine learning models and cross-institution risk analytics frameworks for regulated markets — advancing the academic and scientific foundation of institutional tokenization."}, {"id": "hashlock", "name": "Hashlock", "tag": "SECURITY", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/hashlock-Fo8KZBPk.png", "text": "Hashlock is a leading blockchain security firm providing smart contract auditing, penetration testing, and security advisory services. As a T-RIZE ecosystem partner, Hashlock contributes to the security infrastructure that underpins T-RIZE's on-chain deployments — ensuring that smart contracts governing tokenized assets meet the highest security standards before institutional capital is committed. Their security audits provide an essential layer of assurance for institutional investors evaluating T-RIZE's tokenized instruments."}, {"id": "ekitas", "name": "Ekitas", "tag": "SERVICES", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/ekitas-DkaDd5gO.png", "text": "Ekitas is a Laval-based law firm and tax advisory practice specializing in business law, tax planning, and corporate structuring. In the context of T-RIZE's RWA deals, Ekitas plays a key role in designing the corporate and fiscal frameworks that govern tokenized asset transactions — ensuring that the legal structures surrounding each deal are optimized for investor protection, tax efficiency, and regulatory compliance. Their partnership with T-RIZE reflects the critical importance of sound legal and fiscal architecture in making institutional tokenization both viable and responsible."}]

CANTON_BOARD = [{"id":"madani","name":"Madani Boukalba","role":"T-RIZE Group — Founder & CEO","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/Madani-Boukalba-1.jpg","text":"Madani Boukalba is the Founder & CEO of T-RIZE Group (est. 2022), an institutional market-infrastructure company on the Canton Network with fully deployed workflows for origination, structuring, tokenization, collateralization, and post-trade lifecycle management. T-RIZE has secured over $10B in signed contracts and MOUs and is delivering the first tokenized new-construction real-estate transaction on Canton, alongside one of the largest private-credit tokenizations in the market. T-RIZE specializes in institutional structured products including private credit, funds, securitized debt, construction loans, and convertible digital bonds. Madani co-leads the first Industrial Research Chair in Tokenization & Federated Learning, supported by the Government of Canada. He is co-author of a U.S. patent-pending communication and orchestration protocol. Before T-RIZE, he spent 13 years at CDPQ as a Senior Trader specializing in complex derivatives and collateral optimization."},{"id":"yuval","name":"Yuval Rooz","role":"Digital Asset — Co-Founder & CEO","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/yuval-rooz-1.webp","text":"Yuval Rooz is the Co-Founder and CEO of Digital Asset — the New York-based company behind DAML, the smart contract language powering Canton Network. Digital Asset has raised $300M+ from Goldman Sachs, Citigroup, JPMorgan, and Deutsche Börse. Before Digital Asset, Yuval managed an algorithmic trading desk at DRW and worked as a trader and developer at Citadel. He holds a B.S. in Electrical Engineering from Georgia Tech."},{"id":"jorgen","name":"Jørgen Ouaknine","role":"Euroclear — Group Head of Innovation & Digital Assets","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/jorgen-ouaknine-1.webp","text":"Jørgen Ouaknine has over 20 years of experience in banking and financial services, currently leading Innovation and Digital Assets at Euroclear — the world's largest CSD with €37 trillion in assets under custody. Previously served as CEO of two BNP Paribas subsidiaries in Morocco. His work directly shapes how €37 trillion in assets could eventually migrate onto distributed ledger infrastructure."},{"id":"johnna","name":"Johnna Powell","role":"DTCC — Managing Director, and Head of Technology, Research and Innovation","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/dr-johnna-powell-jpg.jpg","text":"Dr. Johnna Powell is Managing Director, and Head of Technology, Research and Innovation at DTCC. Within this role she drives external and internal innovation through strategic thought leadership, enterprise-wide advocacy, collaborative sponsorship and execution. She has driven instrumental impact in AI, Digital Assets & web3, and Emerging Tech & Next-Gen Capabilities at DTCC. She drives the AI transformation at DTCC, including defining the AI strategy, running execution of enterprise-wide prioritized use cases, establishing AI policies, governance and enablement teams (in close partnership with every DTCC department), responsible for democratizing AI and onboarding impactful GenAI technologies. She also drives enterprise-wide AI upskilling programs and industry-wide innovation events such as hackathons and showcases. Dr. Powell also leads the team responsible for designing, building and executing DTCC's Digital Asset Launchpad, which also led the technical due diligence that enabled DTCC's first blockchain acquisition and integration. Dr. Powell recently was listed among the World Federation of Exchanges' Top Women Leaders for 2025 and the CoinDesk Top 50 Women in Web3 and AI for 2025. She currently serves on the Boards of Fnality, Inc. and serves as the Chair of the Board for both Canton Foundation and Linux Foundation Decentralized Trust. In addition to her work at DTCC, Johnna was the Global Co-Head of ConsenSys NFT and Head of US Professional Services at ConsenSys, a leading blockchain and web3 software company, where she served multiple Fortune 100 clients on product design, development and launch. Her work included bringing to market a carbon credit trading platform with a global alliance of 4 banks, designing a digital securities issuance and lifecycle management platform with 10 of the largest global banks, and launching an NFT ticketing and collection platform with Sports Illustrated and several other Fortune 100 companies. Under her leadership, the ConsenSys NFT team spearheaded several high-profile endeavors in NFT collections across industries, including art, entertainment and music, including marquis collections such as Damien Hirst's: The Currency Project and The Merge: Regenesis. Prior to that, Johnna was a Vice President and Head of User Engagement within GLG, and senior Engagement Manager at McKinsey & Company, where she focused on corporate strategy and commercial engagements including marketing & sales optimization and go-to-market strategies. Dr. Powell holds a PhD in Electrical Engineering & Computer Science from MIT, where she conducted research in millimeter-wave imaging systems and Ultra-Wideband ('5G') antennas for military applications. While at MIT, she won the IEEE International Best Paper Award for her research, along with a patent for her designs. In addition, BMW used her designs and prototypes in their Ultra-Wideband systems testing."},{"id":"german","name":"Germán Soto Sanchez","role":"Broadridge — Chief Product & Strategy Officer","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/German-Soto-Sanchez.jpg","text":"Germán Soto Sanchez is the Chief Product and Strategy Officer of Broadridge Financial Solutions — an S&P 500 company with $24B+ market cap. A direct report to the CEO and member of the Executive Leadership Team, Germán previously led strategic investments at JPMorgan Chase and was President of Cloud9 Technologies. He holds an MBA from Stanford GSB and a B.S. in Mechanical Engineering from MIT."},{"id":"john","name":"John O'Neill","role":"HSBC — Group Head of Digital Assets & Currencies","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/john.jpg","text":"John O'Neill is HSBC's Group Head of Digital Assets and Currencies, leading HSBC Orion — the bank's proprietary digital assets platform enabling financial institutions to issue digital bonds on blockchain. With $3 trillion in total HSBC assets and operations in 60+ countries, John's work directly shapes how one of the world's largest banks integrates tokenized assets into its core banking infrastructure. He has over 20 years of experience in financial services."},{"id":"chris","name":"Chris Zuehlke","role":"Cumberland — Global Head","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/Chris-Zuehlke.jpg","text":"Chris Zuehlke is a Partner at DRW and Global Head of Cumberland, one of the world's leading institutional crypto market makers. Under his leadership, Cumberland has grown into a dominant force in institutional digital asset liquidity — facilitating billions in daily volume for the world's largest banks, hedge funds, and asset managers. Cumberland's market-making capabilities are critical infrastructure for any tokenized asset seeking liquidity."},{"id":"jack","name":"Jack Yang","role":"LTP — Founder & CEO","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/Jack-Yang.jpg","text":"Jack Yang founded LTP in 2021 after building APAC's largest crypto quant fund database. Starting his career as an aircraft engineer, Jack brought aerospace-grade precision to digital finance, building LTP into a top global prime broker delivering secure, regulated infrastructure across execution, settlement, custody, lending, and financing for digital assets."},{"id":"amy","name":"Amy Kalnoki","role":"Bitwave — Co-Founder & COO","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/amy-kalnoki-1.webp","text":"Amy Kalnoki is Co-Founder and COO of Bitwave — the #1 digital asset finance platform for enterprises, with SOC 1 Type 2 and SOC 2 Type 2 compliance. Before Bitwave, she co-founded Synata, an enterprise search startup acquired by Cisco in 2016. Recognized with a CrossTech Innovation Award in 2024, Amy ensures T-RIZE's institutional clients can accurately account for every tokenized transaction."},{"id":"mark","name":"Mark Wendland","role":"Canton Strategic Holdings — Chairman & CEO","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/mark-wendland.webp","text":"Mark Wendland is Chairman and CEO of Canton Strategic Holdings, overseeing digital asset treasury strategy and Super Validator operations. Previously Partner and COO at DRW ($10B+ AUM) and before that led Global Fixed Income Finance at Citadel. His dual background in traditional finance operations and digital asset treasury management makes him uniquely positioned to steward Canton's long-term capital strategy."},{"id":"ryan","name":"Ryan Trinkle","role":"Obsidian Systems — Partner","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/ryan_trinkle.jpg","text":"Ryan Trinkle is co-founder of Obsidian Systems and a longtime Haskell open-source contributor — best known for Reflex FRP. Since DAML (Canton's smart contract language) is based on Haskell, Ryan brings uniquely relevant deep technical expertise. As a Canton Foundation board member, he chairs the Audit and Finance Committee, providing critical governance oversight to the network."},{"id":"patrick","name":"Patrick Corker","role":"BNY Mellon — Managing Director, Digital Assets","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/patrick-corker.jpg","text":"Patrick Corker leads BNY Mellon's pioneering efforts to bridge traditional finance with blockchain-based assets across custody, tokenization, and data integration. With $49.5 trillion in assets under custody at BNY, Patrick's work determines what tokenized RWAs can achieve institutional scale. Before BNY, he was VP and Treasurer at Circle, and held roles at LedgerX, Goldman Sachs, and Deloitte."},{"id":"veronica","name":"Veronica Augustsson","role":"7RIDGE — Partner","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/veronica.jpg","text":"Veronica Augustsson is a Partner at 7RIDGE. Previously CEO of Cinnober — the leading independent trading and clearing technology provider, acquired by Nasdaq for $190M. Named in Institutional Investor's Trading Tech 40, Financial News' 100 Most Influential Women in Finance, and Sweden's Top 10 Most Powerful CEOs. She brings world-class trading infrastructure expertise to Canton's governance."},{"id":"justin","name":"Justin Peterson","role":"Tradeweb — Chief Technology Officer","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/peterson.jpg","text":"Justin Peterson is CTO of Tradeweb Markets — the $25B+ NASDAQ-listed electronic trading platform processing $1.5 trillion+ in daily volume. With 30+ years in computer science and 20+ in financial services, Justin holds a Ph.D. in Computer Science from Georgia Tech. His role on the Canton Foundation board brings world-class electronic trading architecture expertise to Canton's technical governance."},{"id":"kinga","name":"Kinga Bosse","role":"MPCH — Chief Operating Officer","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/kinga_bosse.jpg","text":"Kinga Bosse is COO at MPCH — a leading security firm for businesses and government. Previously CFO at Lukka (digital asset accounting) and held senior roles at Houlihan Lokey, AIG, and State Street. Her 20+ years across finance, insurtech, and fintech provides valuable operational and financial governance expertise to the Canton Foundation board."},{"id":"james","name":"James Lang","role":"Liberty City Ventures — Managing Director","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/James-Lang.jpg","text":"James Lang leads Liberty City Ventures' investment team, with a career spanning hedge fund management, OTC trading at Paxos, and founding-stage involvement at Lukka. Previously managed the $175M portfolio of the Episcopal Diocese of Long Island's Investment Advisory Committee (top-decile returns). He holds a CFA designation, an MBA from NYU, and a B.A. in Economics from Columbia University."},{"id":"jonathan","name":"Jonathan Mayeur","role":"IntellectEU — Head of Product","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/Jonathan-Mayeur.jpg","text":"Jonathan Mayeur is Head of Product at IntellectEU, leading development of the Catalyst Product Suite — actively deployed by Canton Network participants to modernize payments and digital asset adoption at scale. His six years of work on payments and blockchain projects across capital markets, telco, and supply chain provide practical, production-grade insight into Canton's operational challenges."},{"id":"yiannis","name":"Yiannis Varelas","role":"Five North — Founder","img":"https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/yiannis.jpg","text":"Yiannis Varelas leads Five North, a proprietary trading and venture studio building foundational infrastructure for the Canton Network — the basic building blocks used to develop decentralized financial applications and markets. Built from years of engineering, market research, and first-principles risk-taking, Five North sits at the intersection of trading and infrastructure development for institutional blockchain markets."}]

RWA_DEALS = [{"id": "kairos", "name": "Kairos Digital Loan Notes", "subtitle": "KAI18-1 · B+ Rated · Canton Network · $500M Program", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/icon_black_KAIROS_DLN-DrYRiWCm.png", "text": "The Kairos Digital Loan Notes (KAI18-1) represent one of the most sophisticated tokenized debt instruments deployed on a blockchain to date. T-RIZE structured a $500 million digital bond program for Horizon Group on Canton Network, with an initial $50 million tranche available to qualified US and European investors. The instrument is backed by litigation finance receivables from UK motor finance mis-selling claims. Particula Ratings assigned a pre-issuance rating of B+ — the first-ever risk rating issued to a tokenized asset on Canton Network. The structure features a 3-of-6 multi-signature governance model, a 4-step Governance Gateway, Chainlink oracle integration as an external integrity layer, and Fireblocks MPC signature separation — setting a new institutional standard for on-chain structured finance."}, {"id": "vision60", "name": "Vision 60 by Ste-Rose", "subtitle": "$24.2M · 60 Units · Laval, Québec · $200M Program · Republic", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/VISION60.jpg", "text": "Vision 60 by Ste-Rose is a $24.2 million, 60-unit energy-efficient residential development in Laval, Québec — the first new-construction real estate equity offering to be issued natively on the Canton Network and distributed through a fully regulated channel. This is the first tranche of a US$200 million, 500+ unit real estate issuance by T-RIZE Group, distributed through Republic under Regulation D for US accredited investors. T-RIZE structured and tokenized the offering, Canton Network provided the privacy-enabled institutional rails, and Republic's affiliated broker-dealer OpenDeal Broker LLC (Member FINRA | SIPC) handled distribution. Vision 60 provides a repeatable template for scaling compliant real estate capital formation globally — addressing the G20's housing shortfall of roughly 35 million units by 2030."}, {"id": "vision87", "name": "Vision 87 by Champfleury", "subtitle": "$23M · 87 Units · Montréal · $300M Program · Texture Capital", "img": "https://raw.githubusercontent.com/hcftrize/TOKERIZE/dev/assets/VISION87-LOGO-TRANSPARENT.png", "text": "Vision 87 by Champfleury is an 87-unit, $23 million tokenized real estate offering — the first tranche of T-RIZE's $300 million, 956-unit Champfleury residential development in Montréal, one of the largest tokenized new-construction deals in history. Structured by T-RIZE and distributed through Texture Capital (SEC-registered ATS, FINRA member) under Regulation D 506(c), Vision 87 provides accredited investors with regular monthly dividends from rental income and a strategic exit targeting sale or refinancing after five years. Chainlink provides data integrity via CCIP on Avalanche and Base. This deal established T-RIZE's blueprint for scaling compliant institutional real estate tokenization across a multi-billion dollar pipeline."}]


# Build lookup maps for bonus /name and /entity commands
_ALL_ENTITIES = (
    [{**e, "id": e["name"].lower(), "_src": "eco"} for e in TRIZE_ECOSYSTEM] +
    [{**b, "id": b["name"].lower(), "_src": "board"} for b in CANTON_BOARD] +
    [{**d, "id": d["name"].lower(), "_src": "deal"} for d in RWA_DEALS]
)


def _fmt_eco(e: dict) -> str:
    text = e.get("text", "")
    trimmed = text[:4095] + ("..." if len(text) > 4095 else "")
    return f"*{e['name']}*\n_{e['tag']}_\n\n{trimmed}"


def _fmt_board(b: dict) -> str:
    text = b.get("text", "")
    trimmed = text[:4095] + ("..." if len(text) > 4095 else "")
    return f"*{b['name']}*\n_{b['role']}_\n\n{trimmed}"


def _fmt_deal(d: dict) -> str:
    return f"*{d['name']}*\n_{d['subtitle']}_\n\n{d['text']}"


async def lookup_any(query: str) -> str:
    """Used for bonus /name and /entity shortcuts."""
    match = find_entity(query, _ALL_ENTITIES)
    if not match:
        return None
    src = match.get("_src", "eco")
    if src == "board":
        return _fmt_board(match)
    if src == "deal":
        return _fmt_deal(match)
    return _fmt_eco(match)


# ── /ecosystem ────────────────────────────────────────────────────────────────

async def cmd_ecosystem(args: list) -> str:
    if not args:
        lines = [
            "\U0001f310 *T-RIZE Ecosystem Partners*",
            "_Key partners and infrastructure providers in the T-RIZE ecosystem_",
            "",
        ]
        for e in TRIZE_ECOSYSTEM:
            lines.append(f"\u2022 *{e['name']}* \u2014 {e['tag']}")
        lines += ["", "_Reply with a partner name to learn more._"]
        return "\n".join(lines)
    query  = " ".join(args)
    entity = find_entity(query, [{**e, "id": e["name"].lower()} for e in TRIZE_ECOSYSTEM])
    if not entity:
        return f"No T-RIZE ecosystem partner found for *{query}*.\n\nType `/ecosystem` to see all partners."
    return _fmt_eco(entity)


# ── /canton ───────────────────────────────────────────────────────────────────

async def cmd_canton(args: list) -> str:
    if not args:
        return (
            "*Canton Network Ecosystem*\n\n"
            "290+ entities: banks, asset managers, validators, infrastructure.\n\n"
            "Search any entity:\n"
            "`/canton franklin templeton`\n"
            "`/canton deutsche bank`\n"
            "`/canton cumberland`\n"
            "`/canton bnp`\n\n"
            "Type `/cantonlist` to browse all entities."
        )
    query    = " ".join(args)
    entities = await get_entities()
    if not entities:
        return "Could not load Canton entities data."
    entity = find_entity(query, entities)
    if not entity:
        return f"No entity found for *{query}*.\n\nTry: `/canton bnp` `/canton societe` `/canton frax`"

    name = entity.get("name", "Unknown")

    # Clean tags — entities.json has messy tags with duplicates and embedded text
    raw_tags = entity.get("tags", [])
    clean_tags = set()
    for t in raw_tags:
        t = str(t).strip()
        # Skip tags that are multi-line garbage from scraping
        if "\n" in t or len(t) > 40 or "Roles" in t or "Network Utilities" in t or "items found" in t:
            continue
        clean_tags.add(t.upper())
    tag_str = " · ".join(sorted(clean_tags)) if clean_tags else ""

    # Description: prefer detail_text (long desc), fall back to short_desc
    short_desc = entity.get("short_desc", "")
    detail_text = entity.get("detail_text", "")

    if detail_text:
        # Extract real paragraphs from detail_text (skip nav boilerplate)
        lines_dt = [l.strip() for l in detail_text.split("\n") if l.strip()]
        skip = {"canton network", "developers", "use cases", "resources", "speak to an expert",
                "network utilities", "roles", "apps (all)", "apps (featured)"}
        real_lines = []
        for l in lines_dt:
            if l.lower() in skip or l.startswith("http") or l == name:
                continue
            if any(x in l.upper() for x in ["VALIDATOR", "FINANCIAL", "MARKET INFRA", "SERVICE"]) and len(l) < 60:
                continue
            real_lines.append(l)
        desc = " ".join(real_lines[:3])[:600]
        if not desc:
            desc = short_desc
    elif short_desc:
        desc = short_desc
    else:
        desc = ""

    lines = [f"*{name}*"]
    if tag_str:
        lines.append(f"_{tag_str}_")
    lines.append("")
    if desc:
        lines.append(desc)

    return "\n".join(lines)


async def cmd_cantonboard(args: list) -> str:
    if args:
        query = " ".join(args)
        match = find_entity(query, [{**b, "id": b["name"].lower()} for b in CANTON_BOARD])
        if match:
            return _fmt_board(match)
        return f"No board member found matching *{query}*."

    lines = [
        "*Canton Foundation Board Members*",
        f"_{len(CANTON_BOARD)} members_",
        "",
    ]
    for b in CANTON_BOARD:
        lines.append(f"• *{b['name']}* — {b['role']}")
    lines += ["", "_Reply with a name to learn about their background._"]
    return "\n".join(lines)


# ── /rwa ──────────────────────────────────────────────────────────────────────

async def cmd_rwa(args: list) -> str:
    lines = [
        "*T-RIZE RWA Deals*",
        "_Tokenized real-world assets structured by T-RIZE Group_",
        "",
        "*Real Estate*",
        "  `/vision87` — Vision 87 by Champfleury · Montréal · $23M",
        "  `/vision60` — Vision 60 by Ste-Rose · Laval · $24.2M",
        "",
        "*Private Credit*",
        "  `/kairos` — Kairos Digital Loan Notes · $500M Program · B+ Rated",
        "",
        "_Type the command to learn more._",
    ]
    return "\n".join(lines)


# ── /vision87 /vision60 /kairos ───────────────────────────────────────────────

async def cmd_vision87(args: list) -> str:
    d = next((x for x in RWA_DEALS if x["id"] == "vision87"), None)
    return _fmt_deal(d) if d else "Not found."

async def cmd_vision60(args: list) -> str:
    d = next((x for x in RWA_DEALS if x["id"] == "vision60"), None)
    return _fmt_deal(d) if d else "Not found."

async def cmd_kairos(args: list) -> str:
    d = next((x for x in RWA_DEALS if x["id"] == "kairos"), None)
    return _fmt_deal(d) if d else "Not found."
