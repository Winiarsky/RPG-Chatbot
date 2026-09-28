# Przegląd systemu

Przegląd lokalnego kodu i regresji wykonany podczas porządkowania projektu. To działający prototyp jednej przygody z trwałym stanem, a nie kompletny silnik D&D ani aplikacja wieloużytkownikowa.

## Architektura

| Warstwa | Odpowiedzialność |
| --- | --- |
| `app.py`, `manage_rules.py` | Bieżący interfejs gry i sterowanie tym samym kontrolerem |
| `rules7b/` | Integracja konsultacji, audyt źródeł i ograniczenie obsługiwanych reguł |
| `improv6/` | Dopasowanie swobodnego zamiaru do dozwolonych interakcji |
| `story5/` | Sceny, wiedza, rozmowy NPC i wybór zatwierdzonych odpowiedzi |
| `agents4/` | Przebieg tury, checkpointy, wznowienia i role bazowe |
| `mechanics3/` | Przygotowanie prób, rzuty i deterministyczne skutki |
| `storage.py`, `schemas.py`, `public_context.py` | SQLite, walidacja danych i projekcja publicznego stanu |
| `library7a/`, `library_app.py`, `manage_library.py` | Import, przegląd, wyszukiwanie i kontrola cytowań w osobnej bazie |
| `chat_service.py`, `config.py` | Transport modelu, konfiguracja i maskowanie diagnostyki |

Kontrolery i repozytoria dziedziczą zachowanie wcześniejszych warstw. Numery w nazwach są historyczne, ale kod pozostaje używany. Zmiana ich nazw wymaga osobnej migracji importów i sprawdzenia istniejących checkpointów. Usunięto natomiast zastąpione interfejsy, dokumentację dystrybucji etapów, stare sumy archiwów i nadmiarowe skrypty smoke oraz nieużywane adaptery i prompty dawnych interfejsów czatu.

## Co działa dobrze

- LLM zwraca walidowane decyzje. Parametry mechaniki i zmiany świata wykonuje silnik, nie dowolny tekst modelu.
- Oddzielone publiczne i prywatne dane. Opiekun scenariusza dostaje prywatny pakiet poza checkpointem; w trybie openai pakiet trafia do API modelu. Narrator otrzymuje wyłącznie zatwierdzone informacje.
- Rewizje stanu, transakcje, identyfikatory żądań i blokady kampanii chronią przed podwójnym wykonaniem skutków. Testy obejmują restart, błędy, konkurencyjne zapisy i rollback.
- Konsultacje zachowują snapshot źródeł; pobranie nowej wersji podręcznika nie przepisuje historii gry.
- Dostęp do API jest oddzielony od testów i trybu mock. Zachowano istniejące ustawienie `use_responses_api=True`.

## Błędy usunięte w tym przeglądzie

| Problem | Zmiana |
| --- | --- |
| Domyślne pytest pomijało większość systemu, a pełna kolekcja miała kolizje importów | Wspólny katalog testów, jawne pakiety i tryb importlib |
| Instalacja z requirements.txt pomijała zależności bieżącej gry | Jeden pełny zestaw zależności runtime |
| Dane, bazy i pliki lock mogły trafić do commita | Rozszerzone reguły gitignore |
| Słowo dokładnie na granicy limitu generowało pusty fragment | Poprawiony chunker i regresja granicy rozmiaru |
| Hash PDF i parsowana treść mogły pochodzić z różnych odczytów pliku | Parsowanie tego samego bufora bajtów, który podlega SHA-256 |
| Zmiana korpusu podczas wyszukiwania offline mogła zwrócić nieaktualne dowody | Kontrola rewizji po wyszukiwaniu, również przed API |
| Zbyt długie /zasady blokowało aktywną turę błędem walidacji | Zakończenie bez skutków z prośbą o skrócenie pytania |
| Zmiana HP przed potwierdzeniem kontaktu mogła zostawić operację pending | Unieważnienie przez kontrolę stanu podczas zatwierdzania |
| Powtórzone odkrycia w YAML powodowały błąd dopiero przy wykonaniu | Odrzucenie duplikatów już podczas walidacji scenariusza |
| CLI doctor zwracało sukces mimo brakujących pakietów | Niezerowy kod wyjścia i pełniejsza lista zależności |
| Zły setup biblioteki pozwalał UI utworzyć kampanię przed błędem | Sprawdzenie biblioteki i ścieżek przed zapisem kampanii |

## Ograniczenia i dalsze decyzje

Kontrola cytatu sprawdza tożsamość i dosłowność, ale nie zgodność wniosku z zasadą. Prompt ogranicza narratora, lecz nie daje formalnej gwarancji, że tekst nie doda niezatwierdzonego faktu. Obecne testy nie zastępują ewaluacji rzeczywistych modeli.

Korpus startowy obejmuje wybrane sekcje; PDF wymaga ręcznego przeglądu kolejności tekstu, tabel i granic stron. Mechanika nie obejmuje całej walki, czarów, Help ani dynamicznych efektów. Brak materiału lub działania w katalogu nie oznacza zakazu według podręcznika.

SQLite i blokady plikowe są odpowiednie dla lokalnego prototypu. Nie ma kont, uprawnień graczy ani izolacji dla publicznego hostingu. Nie badano obciążenia wielu użytkowników. Zależności mają zakresy wersji; bez lockfile instalacja w przyszłości może pobrać inne wersje.

Nie wykonywano płatnych wywołań modeli ani embeddingów. Nie zmieniano lokalnych zapisów kampanii ani `.env`. Plan dalszych prac zawiera [TODO.md](TODO.md).
