# Experiment records

The records this report cites, copied from `backend/data/research/experiments/` as they
were written. Each is hashed: `sha256` inside the file is the hash of everything else in it,
and `python -m app.research.run experiments` flags a record that no longer matches.

The history they ran on is not in the repository — it is about 18 MB of exchange data, in
`backend/data/research/datasets/`, named by the dataset ids each record lists. To rebuild
it: `python -m app.research.run snapshot BTCUSD ETHUSD SOLUSD DOGEUSD BNBUSD ADAUSD LINKUSD
LTCUSD TRXUSD`. A snapshot taken later holds more bars and so has a different id; the
stretches a study uses are cut by date and are the same.

| Experiment | Kind | Strategy | Market | Timeframe | Charged as | Verdict | Result | sha256 |
|---|---|---|---|---|---|---|---|---|
| `exp-20261012-130241-863284` | study | candidates | UNIVERSE | 1h,4h,1d | crypto_perp,crypto_spot | REJECTED | 198 configurations; 3 insufficient evidence, 27 rejected | `971a1c3b0a17` |
| `exp-20261012-130832-e11ca8` | diagnostic | candidates | UNIVERSE | 1h,4h,1d | crypto_perp,crypto_spot | NOT A VERDICT | 198 configurations ≈ 15.46 independent | `f69359737ff7` |
| `exp-20261012-140538-138bf2` | engine_walk_forward | engine | BTCUSD | 1h | generic | NO MEASURED EDGE | 112 trades, -0.111R (-0.314 … +0.096) | `650b20c1c61a` |
| `exp-20261012-140556-e21cd2` | engine_walk_forward | engine | BTCUSD | 1h | crypto_perp | NO MEASURED EDGE | 112 trades, -0.110R (-0.316 … +0.102) | `f53aaeb22b6d` |
| `exp-20261012-140604-8db980` | engine_walk_forward | engine | BTCUSD | 1h | crypto_spot | NO MEASURED EDGE | 16 trades, -0.728R (-1.237 … -0.188) | `383750692deb` |
| `exp-20261012-140752-55154f` | registration | donchian_trend | UNIVERSE | 4h | crypto_perp | WATCHING | frozen {'k': 4.0, 'n': 100.0} from 2026-10-12 | `4a67d4692e5f` |
| `exp-20261012-140756-c38be3` | registration | donchian_trend | UNIVERSE | 4h | crypto_spot | WATCHING | frozen {'k': 4.0, 'n': 100.0} from 2026-10-12 | `6804d988e1f9` |
| `exp-20261012-140759-da0b7f` | registration | donchian_trend | UNIVERSE | 1d | crypto_spot | WATCHING | frozen {'k': 4.0, 'n': 20.0} from 2026-10-13 | `169f2c0ac1ad` |
| `exp-20261012-140954-6b7513` | engine_walk_forward | engine | ETHUSD | 1h | generic | NO MEASURED EDGE | 51 trades, +0.217R (-0.137 … +0.584) | `e851af8027e0` |
| `exp-20261012-141005-0e7257` | engine_walk_forward | engine | ETHUSD | 1h | crypto_perp | NO MEASURED EDGE | 51 trades, +0.226R (-0.134 … +0.600) | `50eb70c93651` |
| `exp-20261012-141015-70e995` | engine_walk_forward | engine | ETHUSD | 1h | crypto_spot | NO MEASURED EDGE | 35 trades, -0.479R (-0.922 … -0.021) | `c0da7a0e52e4` |
| `exp-20261012-141335-13f389` | engine_walk_forward | engine | SOLUSD | 1h | generic | NO MEASURED EDGE | 86 trades, -0.135R (-0.358 … +0.103) | `dae1915e963e` |
| `exp-20261012-141346-506797` | engine_walk_forward | engine | SOLUSD | 1h | crypto_perp | NO MEASURED EDGE | 86 trades, -0.132R (-0.356 … +0.110) | `b6b495b9b5b5` |
| `exp-20261012-141354-37904c` | engine_walk_forward | engine | SOLUSD | 1h | crypto_spot | NO MEASURED EDGE | 31 trades, -0.112R (-0.552 … +0.339) | `c1368dbe378d` |
| `exp-20261012-141614-ea32de` | study | candidates | UNIVERSE | 1h,4h,1d | crypto_perp,crypto_spot | REJECTED | 198 configurations; 3 insufficient evidence, 27 rejected | `796a11cef34a` |
