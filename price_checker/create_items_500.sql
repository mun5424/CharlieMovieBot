PRAGMA foreign_keys = ON;

-- =========================================================
-- Make seeding idempotent (prevents duplicates on rerun)
-- =========================================================
CREATE UNIQUE INDEX IF NOT EXISTS idx_products_category_name
ON products(category, name);

-- =========================================================
-- Helper: seed CPUs (~60)
-- =========================================================
WITH cpu_data(j) AS (
  VALUES (
'[
  {"brand":"AMD","model":"Ryzen 5 3600","name":"AMD Ryzen 5 3600","attrs":{"socket":"AM4"}},
  {"brand":"AMD","model":"Ryzen 5 5500","name":"AMD Ryzen 5 5500","attrs":{"socket":"AM4"}},
  {"brand":"AMD","model":"Ryzen 5 5600","name":"AMD Ryzen 5 5600","attrs":{"socket":"AM4"}},
  {"brand":"AMD","model":"Ryzen 5 5600X","name":"AMD Ryzen 5 5600X","attrs":{"socket":"AM4"}},
  {"brand":"AMD","model":"Ryzen 7 5700X","name":"AMD Ryzen 7 5700X","attrs":{"socket":"AM4"}},
  {"brand":"AMD","model":"Ryzen 7 5800X","name":"AMD Ryzen 7 5800X","attrs":{"socket":"AM4"}},
  {"brand":"AMD","model":"Ryzen 7 5800X3D","name":"AMD Ryzen 7 5800X3D","attrs":{"socket":"AM4","x3d":true}},
  {"brand":"AMD","model":"Ryzen 9 5900X","name":"AMD Ryzen 9 5900X","attrs":{"socket":"AM4"}},
  {"brand":"AMD","model":"Ryzen 9 5950X","name":"AMD Ryzen 9 5950X","attrs":{"socket":"AM4"}},

  {"brand":"AMD","model":"Ryzen 5 7500F","name":"AMD Ryzen 5 7500F","attrs":{"socket":"AM5"}},
  {"brand":"AMD","model":"Ryzen 5 7600","name":"AMD Ryzen 5 7600","attrs":{"socket":"AM5"}},
  {"brand":"AMD","model":"Ryzen 5 7600X","name":"AMD Ryzen 5 7600X","attrs":{"socket":"AM5"}},
  {"brand":"AMD","model":"Ryzen 7 7700","name":"AMD Ryzen 7 7700","attrs":{"socket":"AM5"}},
  {"brand":"AMD","model":"Ryzen 7 7700X","name":"AMD Ryzen 7 7700X","attrs":{"socket":"AM5"}},
  {"brand":"AMD","model":"Ryzen 7 7800X3D","name":"AMD Ryzen 7 7800X3D","attrs":{"socket":"AM5","x3d":true}},
  {"brand":"AMD","model":"Ryzen 9 7900","name":"AMD Ryzen 9 7900","attrs":{"socket":"AM5"}},
  {"brand":"AMD","model":"Ryzen 9 7900X","name":"AMD Ryzen 9 7900X","attrs":{"socket":"AM5"}},
  {"brand":"AMD","model":"Ryzen 9 7900X3D","name":"AMD Ryzen 9 7900X3D","attrs":{"socket":"AM5","x3d":true}},
  {"brand":"AMD","model":"Ryzen 9 7950X","name":"AMD Ryzen 9 7950X","attrs":{"socket":"AM5"}},
  {"brand":"AMD","model":"Ryzen 9 7950X3D","name":"AMD Ryzen 9 7950X3D","attrs":{"socket":"AM5","x3d":true}},

  {"brand":"AMD","model":"Ryzen 5 8600G","name":"AMD Ryzen 5 8600G","attrs":{"socket":"AM5","apu":true}},
  {"brand":"AMD","model":"Ryzen 7 8700G","name":"AMD Ryzen 7 8700G","attrs":{"socket":"AM5","apu":true}},

  {"brand":"Intel","model":"Core i3-12100F","name":"Intel Core i3-12100F","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i5-12400F","name":"Intel Core i5-12400F","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i5-12600K","name":"Intel Core i5-12600K","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i7-12700K","name":"Intel Core i7-12700K","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i9-12900K","name":"Intel Core i9-12900K","attrs":{"socket":"LGA1700"}},

  {"brand":"Intel","model":"Core i5-13400F","name":"Intel Core i5-13400F","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i5-13600K","name":"Intel Core i5-13600K","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i7-13700K","name":"Intel Core i7-13700K","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i9-13900K","name":"Intel Core i9-13900K","attrs":{"socket":"LGA1700"}},

  {"brand":"Intel","model":"Core i5-14400F","name":"Intel Core i5-14400F","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i5-14600K","name":"Intel Core i5-14600K","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i7-14700K","name":"Intel Core i7-14700K","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i9-14900K","name":"Intel Core i9-14900K","attrs":{"socket":"LGA1700"}},

  {"brand":"Intel","model":"Core i5-13500","name":"Intel Core i5-13500","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i7-13700","name":"Intel Core i7-13700","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i5-14500","name":"Intel Core i5-14500","attrs":{"socket":"LGA1700"}},
  {"brand":"Intel","model":"Core i7-14700","name":"Intel Core i7-14700","attrs":{"socket":"LGA1700"}}
]'
  )
),
cpu_rows AS (
  SELECT
    json_extract(value,'$.brand') AS brand,
    json_extract(value,'$.model') AS model,
    json_extract(value,'$.name')  AS name,
    json_extract(value,'$.attrs') AS attrs
  FROM json_each((SELECT j FROM cpu_data))
)
INSERT OR IGNORE INTO products(category, brand, model, name, attrs_json)
SELECT
  'cpu',
  brand,
  model,
  name,
  json_object(
    'query', name,
    'must_not', json('["bundle","combo","for parts","broken"]'),
    'attrs', json(attrs)
  )
FROM cpu_rows;

-- =========================================================
-- GPUs (~120) using chip+VRAM SKUs + variants (AIB-agnostic)
-- =========================================================
WITH gpu_chips(j) AS (
  VALUES(
'[
  {"brand":"NVIDIA","chip":"RTX 3050","vram_gb":8},
  {"brand":"NVIDIA","chip":"RTX 3060","vram_gb":12},
  {"brand":"NVIDIA","chip":"RTX 3060 Ti","vram_gb":8},
  {"brand":"NVIDIA","chip":"RTX 3070","vram_gb":8},
  {"brand":"NVIDIA","chip":"RTX 3070 Ti","vram_gb":8},
  {"brand":"NVIDIA","chip":"RTX 3080","vram_gb":10},
  {"brand":"NVIDIA","chip":"RTX 3080","vram_gb":12},
  {"brand":"NVIDIA","chip":"RTX 3090","vram_gb":24},
  {"brand":"NVIDIA","chip":"RTX 3090 Ti","vram_gb":24},

  {"brand":"NVIDIA","chip":"RTX 4060","vram_gb":8},
  {"brand":"NVIDIA","chip":"RTX 4060 Ti","vram_gb":8},
  {"brand":"NVIDIA","chip":"RTX 4060 Ti","vram_gb":16},
  {"brand":"NVIDIA","chip":"RTX 4070","vram_gb":12},
  {"brand":"NVIDIA","chip":"RTX 4070 SUPER","vram_gb":12},
  {"brand":"NVIDIA","chip":"RTX 4070 Ti","vram_gb":12},
  {"brand":"NVIDIA","chip":"RTX 4070 Ti SUPER","vram_gb":16},
  {"brand":"NVIDIA","chip":"RTX 4080","vram_gb":16},
  {"brand":"NVIDIA","chip":"RTX 4080 SUPER","vram_gb":16},
  {"brand":"NVIDIA","chip":"RTX 4090","vram_gb":24},

  {"brand":"AMD","chip":"RX 6600","vram_gb":8},
  {"brand":"AMD","chip":"RX 6600 XT","vram_gb":8},
  {"brand":"AMD","chip":"RX 6650 XT","vram_gb":8},
  {"brand":"AMD","chip":"RX 6700 XT","vram_gb":12},
  {"brand":"AMD","chip":"RX 6750 XT","vram_gb":12},
  {"brand":"AMD","chip":"RX 6800","vram_gb":16},
  {"brand":"AMD","chip":"RX 6800 XT","vram_gb":16},
  {"brand":"AMD","chip":"RX 6900 XT","vram_gb":16},
  {"brand":"AMD","chip":"RX 6950 XT","vram_gb":16},

  {"brand":"AMD","chip":"RX 7600","vram_gb":8},
  {"brand":"AMD","chip":"RX 7600 XT","vram_gb":16},
  {"brand":"AMD","chip":"RX 7700 XT","vram_gb":12},
  {"brand":"AMD","chip":"RX 7800 XT","vram_gb":16},
  {"brand":"AMD","chip":"RX 7900 GRE","vram_gb":16},
  {"brand":"AMD","chip":"RX 7900 XT","vram_gb":20},
  {"brand":"AMD","chip":"RX 7900 XTX","vram_gb":24}
]'
  )
),
chips AS (
  SELECT
    json_extract(value,'$.brand') AS brand,
    json_extract(value,'$.chip')  AS chip,
    json_extract(value,'$.vram_gb') AS vram_gb
  FROM json_each((SELECT j FROM gpu_chips))
),
variants(suffix, trust_floor) AS (
  VALUES
    ('(any AIB)', 0.85),
    ('(MSI/ASUS/Gigabyte)', 0.90),
    ('(Founders/Reference)', 0.90)
),
gpu_rows AS (
  SELECT
    brand,
    chip,
    vram_gb,
    suffix,
    trust_floor,
    (brand || ' ' || chip || ' ' || vram_gb || 'GB ' || suffix) AS name,
    (chip || ' ' || vram_gb || 'GB') AS query
  FROM chips CROSS JOIN variants
)
INSERT OR IGNORE INTO products(category, brand, model, name, attrs_json)
SELECT
  'gpu',
  brand,
  chip || ' ' || vram_gb || 'GB',
  name,
  json_object(
    'query', query,
    'vram_gb', vram_gb,
    'seller_trust_required', 1,
    'trust_floor', trust_floor,
    'must_not', json('["laptop","bare pcb","mining","for parts","broken","box only"]')
  )
FROM gpu_rows;

-- =========================================================
-- SSDs (~80): NVMe (20 models x 1/2/4TB = 60) + SATA (10 x 1/2TB = 20)
-- =========================================================
WITH nvme_models(j) AS (VALUES(
'[
  {"brand":"Samsung","model":"970 EVO Plus"},
  {"brand":"Samsung","model":"980 PRO"},
  {"brand":"Samsung","model":"990 PRO"},
  {"brand":"WD","model":"Black SN770"},
  {"brand":"WD","model":"Black SN850X"},
  {"brand":"Crucial","model":"P5 Plus"},
  {"brand":"Crucial","model":"T500"},
  {"brand":"Kingston","model":"KC3000"},
  {"brand":"Kingston","model":"NV2"},
  {"brand":"Solidigm","model":"P44 Pro"},
  {"brand":"Sabrent","model":"Rocket 4 Plus"},
  {"brand":"SK hynix","model":"Gold P31"},
  {"brand":"Seagate","model":"FireCuda 530"},
  {"brand":"Corsair","model":"MP600 Pro LPX"},
  {"brand":"TeamGroup","model":"MP44"},
  {"brand":"ADATA","model":"XPG Gammix S70 Blade"},
  {"brand":"Inland","model":"Performance Plus"},
  {"brand":"Lexar","model":"NM790"},
  {"brand":"PNY","model":"CS3140"},
  {"brand":"Silicon Power","model":"XS70"}
]'
)),
sata_models(j) AS (VALUES(
'[
  {"brand":"Samsung","model":"870 EVO"},
  {"brand":"Crucial","model":"MX500"},
  {"brand":"WD","model":"Blue SA510"},
  {"brand":"SanDisk","model":"Ultra 3D"},
  {"brand":"Kingston","model":"A400"},
  {"brand":"TeamGroup","model":"GX2"},
  {"brand":"ADATA","model":"SU800"},
  {"brand":"PNY","model":"CS900"},
  {"brand":"Seagate","model":"BarraCuda SATA SSD"},
  {"brand":"SK hynix","model":"Gold S31"}
]'
)),
nvme AS (
  SELECT json_extract(value,'$.brand') AS brand,
         json_extract(value,'$.model') AS model
  FROM json_each((SELECT j FROM nvme_models))
),
sata AS (
  SELECT json_extract(value,'$.brand') AS brand,
         json_extract(value,'$.model') AS model
  FROM json_each((SELECT j FROM sata_models))
),
caps_nvme(tb) AS (VALUES(1),(2),(4)),
caps_sata(tb) AS (VALUES(1),(2))
INSERT OR IGNORE INTO products(category, brand, model, name, attrs_json)
SELECT
  'ssd',
  nvme.brand,
  nvme.model,
  nvme.brand || ' ' || nvme.model || ' ' || caps_nvme.tb || 'TB NVMe' AS name,
  json_object(
    'query', nvme.brand || ' ' || nvme.model || ' ' || caps_nvme.tb || 'TB NVMe',
    'interface', 'NVMe',
    'capacity_tb', caps_nvme.tb,
    'must_not', json('["enclosure","external","heatsink only","case only"]')
  )
FROM nvme CROSS JOIN caps_nvme
UNION ALL
SELECT
  'ssd',
  sata.brand,
  sata.model,
  sata.brand || ' ' || sata.model || ' ' || caps_sata.tb || 'TB SATA' AS name,
  json_object(
    'query', sata.brand || ' ' || sata.model || ' ' || caps_sata.tb || 'TB SATA',
    'interface', 'SATA',
    'capacity_tb', caps_sata.tb,
    'must_not', json('["enclosure","external"]')
  )
FROM sata CROSS JOIN caps_sata;

-- =========================================================
-- RAM (~60): DDR5 (40) + DDR4 (20)
-- =========================================================
WITH brands(j) AS (VALUES('["G.Skill","Corsair","Kingston","TeamGroup","Crucial"]')),
b AS (SELECT value AS brand FROM json_each((SELECT j FROM brands))),
ddr5_kits(gb) AS (VALUES(32),(64)),
ddr5_speeds(mhz) AS (VALUES(5600),(6000)),
ddr5_cls(cl) AS (VALUES(30),(36)),
ddr4_kits(gb) AS (VALUES(32)),
ddr4_speeds(mhz) AS (VALUES(3200),(3600)),
ddr4_cls(cl) AS (VALUES(16),(18))
INSERT OR IGNORE INTO products(category, brand, model, name, attrs_json)
SELECT
  'ram',
  brand,
  'DDR5',
  brand || ' DDR5 ' || gb || 'GB (2x' || (gb/2) || 'GB) ' || mhz || ' CL' || cl AS name,
  json_object(
    'query', brand || ' DDR5 ' || gb || 'GB kit ' || mhz || ' CL' || cl,
    'ddr', 'DDR5',
    'kit_gb', gb,
    'speed_mhz', mhz,
    'cl', cl,
    'must_not', json('["single stick","laptop","sodimm","for parts","broken"]')
  )
FROM b CROSS JOIN ddr5_kits CROSS JOIN ddr5_speeds CROSS JOIN ddr5_cls
UNION ALL
SELECT
  'ram',
  brand,
  'DDR4',
  brand || ' DDR4 ' || gb || 'GB (2x' || (gb/2) || 'GB) ' || mhz || ' CL' || cl AS name,
  json_object(
    'query', brand || ' DDR4 ' || gb || 'GB kit ' || mhz || ' CL' || cl,
    'ddr', 'DDR4',
    'kit_gb', gb,
    'speed_mhz', mhz,
    'cl', cl,
    'must_not', json('["single stick","laptop","sodimm","for parts","broken"]')
  )
FROM b CROSS JOIN ddr4_kits CROSS JOIN ddr4_speeds CROSS JOIN ddr4_cls;

-- =========================================================
-- Motherboards (~70): curated real-world names (keeps matching sane)
-- =========================================================
WITH mobo_data(j) AS (VALUES(
'[
  {"brand":"ASUS","model":"ROG STRIX B650E-F GAMING WIFI"},
  {"brand":"ASUS","model":"TUF GAMING B650-PLUS WIFI"},
  {"brand":"ASUS","model":"PRIME B650-PLUS"},
  {"brand":"ASUS","model":"ROG STRIX X670E-E GAMING WIFI"},
  {"brand":"ASUS","model":"TUF GAMING X670E-PLUS WIFI"},
  {"brand":"ASUS","model":"ROG STRIX B550-F GAMING WIFI II"},
  {"brand":"ASUS","model":"TUF GAMING B550-PLUS WIFI II"},
  {"brand":"ASUS","model":"PRIME B550M-A WIFI II"},

  {"brand":"MSI","model":"MAG B650 TOMAHAWK WIFI"},
  {"brand":"MSI","model":"PRO B650-P WIFI"},
  {"brand":"MSI","model":"MPG X670E CARBON WIFI"},
  {"brand":"MSI","model":"MAG X670E TOMAHAWK WIFI"},
  {"brand":"MSI","model":"MAG B550 TOMAHAWK"},
  {"brand":"MSI","model":"B550-A PRO"},

  {"brand":"Gigabyte","model":"B650 AORUS ELITE AX"},
  {"brand":"Gigabyte","model":"B650M AORUS ELITE AX"},
  {"brand":"Gigabyte","model":"X670E AORUS MASTER"},
  {"brand":"Gigabyte","model":"X670 AORUS ELITE AX"},
  {"brand":"Gigabyte","model":"B550 AORUS ELITE AX V2"},
  {"brand":"Gigabyte","model":"B550M DS3H"},

  {"brand":"ASRock","model":"B650E PG Riptide WiFi"},
  {"brand":"ASRock","model":"B650M Pro RS WiFi"},
  {"brand":"ASRock","model":"X670E Steel Legend"},
  {"brand":"ASRock","model":"B550 Steel Legend"},
  {"brand":"ASRock","model":"B550M Pro4"},

  {"brand":"ASUS","model":"ROG STRIX B760-A GAMING WIFI"},
  {"brand":"ASUS","model":"TUF GAMING B760-PLUS WIFI"},
  {"brand":"ASUS","model":"ROG STRIX Z790-E GAMING WIFI"},
  {"brand":"ASUS","model":"TUF GAMING Z790-PLUS WIFI"},
  {"brand":"ASUS","model":"PRIME Z790-P WIFI"},
  {"brand":"ASUS","model":"PRIME B760-PLUS"},

  {"brand":"MSI","model":"MAG B760 TOMAHAWK WIFI"},
  {"brand":"MSI","model":"PRO B760-P WIFI"},
  {"brand":"MSI","model":"MAG Z790 TOMAHAWK WIFI"},
  {"brand":"MSI","model":"PRO Z790-P WIFI"},
  {"brand":"MSI","model":"B760 GAMING PLUS WIFI"},

  {"brand":"Gigabyte","model":"B760 AORUS ELITE AX"},
  {"brand":"Gigabyte","model":"Z790 AORUS ELITE AX"},
  {"brand":"Gigabyte","model":"Z790 AORUS MASTER"},
  {"brand":"Gigabyte","model":"Z790 UD AX"},
  {"brand":"Gigabyte","model":"B760M DS3H AX"},

  {"brand":"ASRock","model":"B760M Steel Legend WiFi"},
  {"brand":"ASRock","model":"Z790 Steel Legend WiFi"},
  {"brand":"ASRock","model":"Z790 Pro RS"},
  {"brand":"ASRock","model":"Z790 PG Lightning"}
]'
)),
rows AS (
  SELECT
    json_extract(value,'$.brand') AS brand,
    json_extract(value,'$.model') AS model
  FROM json_each((SELECT j FROM mobo_data))
)
INSERT OR IGNORE INTO products(category, brand, model, name, attrs_json)
SELECT
  'motherboard',
  brand,
  model,
  brand || ' ' || model AS name,
  json_object(
    'query', brand || ' ' || model,
    'must_not', json('["bundle","combo","for parts","broken"]')
  )
FROM rows;

-- =========================================================
-- PSUs (~45)
-- =========================================================
WITH psu_data(j) AS (VALUES(
'[
  {"brand":"Corsair","model":"RM750x"},
  {"brand":"Corsair","model":"RM850x"},
  {"brand":"Corsair","model":"RM1000x"},
  {"brand":"Corsair","model":"RMe 750e"},
  {"brand":"Corsair","model":"RMe 850e"},
  {"brand":"Corsair","model":"HX1000i"},

  {"brand":"Seasonic","model":"FOCUS GX-750"},
  {"brand":"Seasonic","model":"FOCUS GX-850"},
  {"brand":"Seasonic","model":"FOCUS GX-1000"},
  {"brand":"Seasonic","model":"PRIME TX-1000"},
  {"brand":"Seasonic","model":"VERTEX GX-850"},
  {"brand":"Seasonic","model":"VERTEX GX-1000"},

  {"brand":"be quiet!","model":"Straight Power 12 750W"},
  {"brand":"be quiet!","model":"Straight Power 12 850W"},
  {"brand":"be quiet!","model":"Dark Power 13 850W"},
  {"brand":"be quiet!","model":"Dark Power 13 1000W"},

  {"brand":"EVGA","model":"SuperNOVA 750 G6"},
  {"brand":"EVGA","model":"SuperNOVA 850 G6"},
  {"brand":"EVGA","model":"SuperNOVA 1000 G6"},

  {"brand":"MSI","model":"MPG A750G PCIe5"},
  {"brand":"MSI","model":"MPG A850G PCIe5"},
  {"brand":"MSI","model":"MEG Ai1000P PCIe5"},

  {"brand":"Thermaltake","model":"Toughpower GF3 750W"},
  {"brand":"Thermaltake","model":"Toughpower GF3 850W"},
  {"brand":"Thermaltake","model":"Toughpower GF3 1000W"},

  {"brand":"Super Flower","model":"Leadex III Gold 750W"},
  {"brand":"Super Flower","model":"Leadex III Gold 850W"},
  {"brand":"Super Flower","model":"Leadex VII XG 1000W"},

  {"brand":"Cooler Master","model":"V750 Gold V2"},
  {"brand":"Cooler Master","model":"V850 Gold V2"},

  {"brand":"NZXT","model":"C750 Gold"},
  {"brand":"NZXT","model":"C850 Gold"},

  {"brand":"ASUS","model":"ROG STRIX 850G"},
  {"brand":"ASUS","model":"ROG THOR 1000W Platinum"},

  {"brand":"Corsair","model":"SF750 (SFX)"},
  {"brand":"Cooler Master","model":"V850 SFX Gold"},
  {"brand":"Lian Li","model":"SP850 (SFX)"},

  {"brand":"Gigabyte","model":"UD850GM PG5"},
  {"brand":"Gigabyte","model":"UD1000GM PG5"},
  {"brand":"DeepCool","model":"PX850G"},
  {"brand":"DeepCool","model":"PX1000G"}
]'
)),
rows AS (
  SELECT json_extract(value,'$.brand') AS brand,
         json_extract(value,'$.model') AS model
  FROM json_each((SELECT j FROM psu_data))
)
INSERT OR IGNORE INTO products(category, brand, model, name, attrs_json)
SELECT
  'psu',
  brand,
  model,
  brand || ' ' || model AS name,
  json_object(
    'query', brand || ' ' || model,
    'must_not', json('["used","refurb","for parts","broken"]')
  )
FROM rows;

-- =========================================================
-- Coolers (~35)
-- =========================================================
WITH cooler_data(j) AS (VALUES(
'[
  {"brand":"Noctua","model":"NH-D15"},
  {"brand":"Noctua","model":"NH-U12A"},
  {"brand":"Noctua","model":"NH-U12S"},
  {"brand":"Noctua","model":"NH-L9a-AM5"},
  {"brand":"Noctua","model":"NH-D15 chromax.black"},

  {"brand":"be quiet!","model":"Dark Rock Pro 5"},
  {"brand":"be quiet!","model":"Dark Rock Elite"},
  {"brand":"be quiet!","model":"Pure Rock 2"},
  {"brand":"be quiet!","model":"Pure Loop 2 240mm"},
  {"brand":"be quiet!","model":"Pure Loop 2 360mm"},

  {"brand":"DeepCool","model":"AK620"},
  {"brand":"DeepCool","model":"AK500"},
  {"brand":"DeepCool","model":"AK400"},
  {"brand":"DeepCool","model":"LS520"},
  {"brand":"DeepCool","model":"LS720"},

  {"brand":"Arctic","model":"Liquid Freezer II 240"},
  {"brand":"Arctic","model":"Liquid Freezer II 280"},
  {"brand":"Arctic","model":"Liquid Freezer II 360"},
  {"brand":"Arctic","model":"Freezer 36"},

  {"brand":"Corsair","model":"iCUE H100i ELITE"},
  {"brand":"Corsair","model":"iCUE H150i ELITE"},

  {"brand":"NZXT","model":"Kraken 240"},
  {"brand":"NZXT","model":"Kraken 360"},

  {"brand":"Lian Li","model":"Galahad II Trinity 240"},
  {"brand":"Lian Li","model":"Galahad II Trinity 360"},

  {"brand":"Thermalright","model":"Peerless Assassin 120 SE"},
  {"brand":"Thermalright","model":"Phantom Spirit 120 SE"},

  {"brand":"Cooler Master","model":"Hyper 212"},
  {"brand":"Cooler Master","model":"MasterLiquid ML240L"},
  {"brand":"Cooler Master","model":"MasterLiquid ML360L"},

  {"brand":"EK","model":"Nucleus AIO CR240"},
  {"brand":"EK","model":"Nucleus AIO CR360"},
  {"brand":"ID-COOLING","model":"SE-224-XTS"},
  {"brand":"Phanteks","model":"Glacier One 360MP"}
]'
)),
rows AS (
  SELECT json_extract(value,'$.brand') AS brand,
         json_extract(value,'$.model') AS model
  FROM json_each((SELECT j FROM cooler_data))
)
INSERT OR IGNORE INTO products(category, brand, model, name, attrs_json)
SELECT
  'cooler',
  brand,
  model,
  brand || ' ' || model AS name,
  json_object('query', brand || ' ' || model)
FROM rows;

-- =========================================================
-- Cases (~30)
-- =========================================================
WITH case_data(j) AS (VALUES(
'[
  {"brand":"Lian Li","model":"LANCOOL 216"},
  {"brand":"Lian Li","model":"LANCOOL III"},
  {"brand":"Lian Li","model":"O11 Dynamic EVO"},
  {"brand":"Lian Li","model":"O11 Air Mini"},

  {"brand":"Fractal Design","model":"North"},
  {"brand":"Fractal Design","model":"Meshify 2"},
  {"brand":"Fractal Design","model":"Torrent"},
  {"brand":"Fractal Design","model":"Pop Air"},

  {"brand":"NZXT","model":"H5 Flow"},
  {"brand":"NZXT","model":"H7 Flow"},
  {"brand":"NZXT","model":"H9 Flow"},

  {"brand":"Corsair","model":"4000D Airflow"},
  {"brand":"Corsair","model":"5000D Airflow"},
  {"brand":"Corsair","model":"2000D Airflow"},

  {"brand":"Phanteks","model":"Eclipse G360A"},
  {"brand":"Phanteks","model":"Eclipse P400A"},
  {"brand":"Phanteks","model":"NV5"},

  {"brand":"be quiet!","model":"Pure Base 500DX"},
  {"brand":"be quiet!","model":"Silent Base 802"},

  {"brand":"Cooler Master","model":"NR200"},
  {"brand":"Cooler Master","model":"TD500 Mesh"},

  {"brand":"HYTE","model":"Y60"},
  {"brand":"HYTE","model":"Y40"},

  {"brand":"Montech","model":"AIR 903 MAX"},
  {"brand":"Montech","model":"SKY TWO"},

  {"brand":"DeepCool","model":"CH560"},
  {"brand":"DeepCool","model":"CC560"},

  {"brand":"Thermaltake","model":"The Tower 300"},
  {"brand":"Thermaltake","model":"The Tower 500"},

  {"brand":"ASUS","model":"Prime AP201"}
]'
)),
rows AS (
  SELECT json_extract(value,'$.brand') AS brand,
         json_extract(value,'$.model') AS model
  FROM json_each((SELECT j FROM case_data))
)
INSERT OR IGNORE INTO products(category, brand, model, name, attrs_json)
SELECT
  'case',
  brand,
  model,
  brand || ' ' || model AS name,
  json_object(
    'query', brand || ' ' || model,
    'must_not', json('["bare chassis","for parts","broken"]')
  )
FROM rows;

-- =========================================================
-- Sanity checks
-- =========================================================
SELECT category, COUNT(*) AS cnt
FROM products
GROUP BY category
ORDER BY category;

SELECT COUNT(*) AS total_products FROM products;