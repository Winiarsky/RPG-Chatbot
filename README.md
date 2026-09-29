# RPG Chatbot

Lokalny prototyp mistrza gry: Streamlit, LangGraph i SQLite. Model rozpoznaje zamiar i tworzy narrację; silnik sprawdza dozwolone działania, rzuty i skutki. Bibliotekarz wyszukuje zasady w osobnej bazie i pokazuje źródła. Interfejs oraz przykładowa przygoda są po polsku.

## Struktura projektu

Kod jest podzielony według odpowiedzialności, bez numerów dawnych etapów w nazwach pakietów:

```text
agents/          role modelu i bazowy przebieg tury w LangGraph
mechanics/       mechanika rzutów i deterministyczne skutki
story/           sceny, wiedza i rozmowy NPC
improvisation/   dopasowanie deklaracji do dozwolonych interakcji
rules/          integracja gry z bibliotekarzem i audyt źródeł
  definitions/  katalog działań oraz profil improwizacji w YAML
library/        import, przegląd i wyszukiwanie materiałów
scenarios/      scenariusze przygód w YAML
characters/     szablony postaci w YAML
materials/      materiały biblioteki i przykładowe manifesty
evals/          zestawy ewaluacyjne bibliotekarza
tests/          wspólne testy wszystkich warstw
```

`app.py` uruchamia grę, a `library_app.py` osobny interfejs biblioteki. Wspólne moduły w katalogu głównym odpowiadają m.in. za konfigurację (`config.py`), transport modelu (`chat_service.py`), zapis stanu (`storage.py`) i schematy danych (`schemas.py`). Kontrolery i repozytoria nadal dziedziczą zachowanie poszczególnych warstw; dalsze uproszczenie tego połączenia opisuje [TODO.md](TODO.md).

## Uruchomienie

Sprawdzane środowisko: Python 3.12. Uruchamiaj polecenia z katalogu projektu.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
python manage_rules.py doctor
python manage_library.py init
python -m streamlit run app.py
```

Bez konfiguracji aplikacja działa w trybie `mock`, lokalnie i bez API. Pierwszy start tworzy kampanię demonstracyjną. `requirements.txt` zawiera wszystkie zależności aplikacji; `requirements-dev.txt` dodaje pytest.

Opcjonalną konfigurację zawiera [.env.example](.env.example). Skopiuj ją do `.env`, jeżeli plik jeszcze nie istnieje. Zmienne środowiskowe mają pierwszeństwo. Do prawdziwej narracji ustaw `LLM_PROVIDER=openai`, własny `OPENAI_API_KEY` i dostępny dla niego `OPENAI_MODEL`. Wywołania API są płatne. Opiekun scenariusza przesyła wtedy do modelu także prywatny pakiet potrzebny do wyboru odpowiedzi NPC. Przykładowy model nie jest automatycznie narzucany istniejącej konfiguracji.

```bash
# Osobny interfejs wyszukiwania i przeglądania źródeł
python -m streamlit run library_app.py

# Wszystkie testy offline oraz samodzielna próba pełnego przepływu
python -m pytest -q
python smoke.py
```

## Rozgrywka

Przykłady w trybie mock: `Rozglądam się`, `Próbuję wyważyć drzwi`, `Pukam`, `Wchodzę do wieży`, `/zasady Jak działa przewaga?`. Mock rozpoznaje ograniczony zestaw fraz i etykiet z katalogu działań; nie jest modelem językowym.

Rzuty można wykonać w aplikacji lub wpisać surowy wynik fizycznego k20. Działania fabularne wymagają zatwierdzenia. Oczekujące decyzje są zapisywane w SQLite i można je wznowić po restarcie. Ponowienie narracji nie powtarza rozstrzygniętych skutków. Nowa kampania zachowuje wcześniejsze zapisy.

`/zasady` i `/rules` wymuszają pytanie o zasady bez wykonania działania. Pytanie ma limit 1200 znaków. Źródła i odpowiedź bibliotekarza są oddzielone od narracji. Znalezienie opisu zasady w podręczniku nie dodaje jej automatycznie do silnika.

## Biblioteka

Pakiet startowy zawiera 16 wybranych sekcji SRD 5.2.1, a nie cały podręcznik. Zachowana atrybucja: [ATTRIBUTION_SRD.md](ATTRIBUTION_SRD.md). Wyszukiwanie domyślnie działa leksykalnie, lokalnie, z aliasami PL/EN.

```bash
python manage_library.py documents
python manage_library.py search 'Jak działa przewaga?'
python manage_library.py ask 'Jak działa przewaga?'
python manage_library.py import materials/examples/manifest.yaml
# Zatwierdź dopiero po sprawdzeniu treści, wersji i metadanych:
python manage_library.py review my_rules_example --approve --confirm
```

Sprawdź identyfikator własnego dokumentu w manifeście lub `documents`. Bez `--llm` komenda `ask` zwraca fragmenty i status `retrieved_only`. Kontrola cytowań potwierdza ich pochodzenie i dosłowność; nie dowodzi poprawności rozumowania modelu.

Opcjonalne embeddingi: `python manage_library.py embeddings --allow-api`. Tryb hybrydowy w grze wymaga gotowego indeksu oraz `RPG7B_SEARCH_MODE=hybrid` i `RPG7B_ALLOW_EMBEDDINGS_API=true`. Generowanie tekstu bibliotekarza można wyłączyć przez `RPG7B_GENERATION=off`. Wyłączenie generowania nie wyłącza API embeddingów przy aktywnej hybrydzie.

## Zapisy i narzędzia

Domyślne bazy: `data/rpg.sqlite3` i `data/library7a.sqlite3`. Muszą mieć różne ścieżki. `.env`, dane, locki i kopie baz są ignorowane przez Git. Bazy zawierają historię gry i prywatne elementy scenariusza.

```bash
python manage_rules.py backup
python manage_rules.py list
python manage_rules.py new --name 'Nowa przygoda'
python manage_rules.py show --campaign demo7b
python manage_rules.py capabilities
python manage_rules.py consultations --campaign demo7b
python manage_rules.py say 'Rozglądam się' --campaign demo7b
```

`manage_rules.py --help` opisuje wznowienie, ponowienie i przerwanie tur. Narzędzia `manage_game.py`, `manage_checks.py`, `manage_graph.py`, `manage_story.py` i `manage_improv.py` pozostają do diagnostyki, korekt oraz obsługi starszych zapisów i checkpointów. Korzystaj z CLI odpowiadającego wersji, która rozpoczęła starszą turę; zakończ ją lub przerwij przed włączeniem integracji.

## Aktualizacja wcześniejszego projektu

Aby kontynuować istniejącą kampanię fabularną, zakończ jej aktywną turę w odpowiednim CLI i włącz integrację jawnie:

```bash
python manage_rules.py backup
python manage_improv.py list
# Dla kampanii fabularnej, która jeszcze nie ma profilu improwizacji:
python manage_improv.py enable --campaign ID
python manage_rules.py enable --campaign ID
python -m streamlit run app.py
```

Zastąp `ID` identyfikatorem własnej kampanii; dla starszych kampanii fabularnych lista jest też dostępna przez `manage_story.py list`. Sam start nowego UI nie konwertuje zapisów. Kampanie sprzed warstwy fabularnej wymagają obsługi dawnym CLI albo rozpoczęcia nowej przygody; automatyczna migracja ich do scenariusza fabularnego nie jest zaimplementowana.

- Główna gra uruchamia się teraz przez `app.py`, a biblioteka przez `library_app.py`. Usunięto historyczne interfejsy etapów i instrukcje nakładania archiwów ZIP.
- Wszystkie zależności są w `requirements.txt`. Testy warstw znajdują się we wspólnym katalogu `tests/` i są uruchamiane domyślnie.
- Pakiety i katalogi zasobów mają nazwy opisujące ich rolę. Importy w kodzie, testach i narzędziach korzystają z nowej struktury. Nazwy tabel, identyfikatory kampanii, prefiksy checkpointów i zmienne środowiskowe są zachowane dla zgodności zapisów oraz konfiguracji. Domyślna baza biblioteki pozostaje w `data/library7a.sqlite3`.
- Poprawka importera ma wersję `paragraphs-v2`. Jeżeli `manage_library.py init` zgłosi konflikt starego startera, jawnie zaktualizuj go przez `python manage_library.py init --replace`. To zastępuje tylko starter; inne dokumenty pozostają. Wcześniej skopiuj bazę biblioteki przy zatrzymanych zapisach. Stare embeddingi zastępowanego dokumentu są usuwane i wymagają odbudowy.
- Własny dokument aktualizuj przez `import MANIFEST --replace`, ponowny przegląd i zatwierdzenie. Zapisane konsultacje zachowują historyczny snapshot źródeł.

Przegląd architektury i ograniczeń: [REVIEW.md](REVIEW.md). Priorytety dalszych prac: [TODO.md](TODO.md). Zakres weryfikacji: [TESTING.md](TESTING.md).
