# Testowanie

Testy działają offline: używają atrap modeli i tymczasowych baz, bez modyfikowania lokalnej kampanii. Interfejs jest sprawdzany przez Streamlit AppTest, a integracje uruchamiają prawdziwy LangGraph i SqliteSaver.

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
python manage_rules.py doctor
python -m pytest -q --durations=15
python smoke.py
```

`pytest.ini` obejmuje domyślnie całe `tests/`, z `--import-mode=importlib`. Testy są pakietami, więc powtarzające się nazwy modułów nie kolidują. Wspólne ustawienia offline są w `tests/conftest.py`; poszczególne warstwy izolują bazy przez `tmp_path`.

| Katalog | Zakres |
| --- | --- |
| `tests/core` | Konfiguracja, historia wiadomości, transport i maskowanie diagnostyki |
| `tests/state` | Schematy, SQLite, rewizje i publiczny kontekst |
| `tests/mechanics` | Walidacja prób, rzuty, skutki, blokady i CLI |
| `tests/graph` | Przebieg tury, restart, idempotencja, retry narracji i rollback |
| `tests/story` | Scenariusze, rozmowy, wiedza, potwierdzenia i sekrety |
| `tests/improvisation` | Dopasowanie działań i kontakty między scenami |
| `tests/library` | Import, wersje korpusu, wyszukiwanie, cytowania, CLI i UI biblioteki |
| `tests/rules` | Pełna integracja konsultacji z grą, audyt oraz bieżący UI |

Skrypty scenariuszy integracyjnych starszych warstw znajdują się przy testach `story` i `improvisation`; są wykonywane przez pytest. Główny `smoke.py` sprawdza konsultację, improwizację, wynik rzutu, restart i brak podwójnych skutków na tymczasowych bazach.

Nowe regresje obejmują granicę chunkowania, spójny odczyt PDF, zmianę korpusu podczas wyszukiwania, jawną aktualizację startera, za długie pytanie o zasady, zmianę HP przed potwierdzeniem rozmowy, duplikaty odkryć w YAML, brak zależności, kopię przy błędnej konfiguracji RAG oraz błędny setup i odtwarzanie UI.

Workflow `.github/workflows/tests.yml` instaluje pełne zależności na Pythonie 3.12 i uruchamia diagnostykę, pytest oraz smoke. W tym przeglądzie sprawdzono polecenia lokalnie; workflow nie był uruchamiany na GitHubie.

## Granice weryfikacji

Testy modeli sprawdzają kontrakty, nie jakość dowolnego rozumowania. Nie wykonywano płatnych wywołań OpenAI ani rzeczywistych embeddingów. Kontrola cytatów nie dowodzi poprawności odpowiedzi. Testy nie są pomiarem wydajności ani testem wieloużytkownikowego wdrożenia.

Niektóre integracje stosują `importorskip`, więc same pominięte testy nie potwierdzają działania środowiska. Diagnostyka oraz smoke powinny również zakończyć się kodem 0.

## Wyniki lokalnej weryfikacji — 2026-09-29

Wyniki dotyczą układu z pakietami `agents`, `mechanics`, `story`, `improvisation`, `rules` i `library` oraz wspólnymi katalogami zasobów.

| Sprawdzenie | Wynik |
| --- | --- |
| `python -m pytest -q --durations=10 --tb=short` | **493 passed**, 426,89 s; bez pominiętych testów |
| Wznowienie checkpointów utworzonych przez kod sprzed reorganizacji | **4/4 przypadki zakończone poprawnie** |
| `python smoke.py` | Kod 0; prawdziwy LangGraph, SQLite, biblioteka i restart rzutu |
| Kompilacja modułów i `git diff --check` | Bez błędów |
| `python -m pip check` | Kod 0, brak niezgodnych zależności |
| `python manage_rules.py doctor` | Kod 0; wszystkie wymagane pakiety dostępne |

Test zgodności uruchomił kod z commita `2a2c6b0` w oddzielnym katalogu i utworzył tymczasowe zapisy. Następnie kod z nowymi nazwami pakietów wznowił: rzut z zapisaną konsultacją, potwierdzenie fabularne, narrację po rozstrzygniętym rzucie oraz nieudaną konsultację zasad. Zachowano identyfikatory oczekujących decyzji. Powtórne wznowienie nie zmieniało świata, historii ani rozstrzygnięć; zapisane konsultacje nie były wykonywane ponownie.

W 37 checkpointach i 206 zapisach pośrednich starego kodu nie znaleziono ścieżek dawnych modułów. Stan grafu wykorzystuje słowniki i wartości podstawowe, dlatego przeniesienie pakietów nie wymaga aliasów dawnych importów. Nazwy tabel, wersje grafów, prefiksy wątków i podpisy danych pozostają częścią zachowanego formatu zapisu.

Porównanie z bazowym commitem potwierdziło obecność wszystkich 139 plików pod ich docelowymi ścieżkami, niezmienioną treść zasobów YAML/JSON i brak starych importów. Nie odczytywano ani nie zmieniano zapisów użytkownika przy teście zgodności.

Zastane środowisko `.venv` zgłasza przy pip ostrzeżenie o niepoprawnej dystrybucji `~ip`; nie blokuje kontroli zależności i nie jest częścią repozytorium. Nie przebudowywano tego lokalnego środowiska ani nie wykonywano instalacji na czystym systemie. Zakresy wersji pozostają zgodne z TODO dotyczącym lockfile.
