# TODO

Priorytety po przeglądzie. Każdy punkt ma kryterium zakończenia; rozszerzenia mechaniki nie są automatycznie obiecywane przez bibliotekę zasad.

## P1 — jakość i powtarzalność

- [ ] **Ewaluacja prawdziwych modeli.** Oddzielić zestaw rozwojowy od pytań testowych; sprawdzić polskie parafrazy, injection, odmowy, wierność narracji oraz odpowiedzi z cytowaniami. Raport powinien zawierać model, wersję promptu, rewizję korpusu, błędne przypadki i koszt.
- [ ] **Powtarzalne zależności.** Wybrać narzędzie do lockfile i zatwierdzić pełne wersje na czystym Pythonie 3.12; CI ma instalować dokładnie ten zestaw.
- [ ] **Procedura odzyskiwania.** Dodać udokumentowaną kopię obu baz, retencję i próbę przywrócenia gry z aktywnym rzutem oraz historyczną konsultacją. Obecne `manage_rules.py backup` obejmuje tylko bazę gry.

## P2 — utrzymanie i skala

- [ ] **Pakiety według odpowiedzialności.** Zastąpić numerowane nazwy modułów semantycznymi nazwami i ograniczyć głęboki łańcuch dziedziczenia. Warunek: stare bazy i aktywne checkpointy nadal się wznawiają albo mają jawną migrację.
- [ ] **Jedno CLI serwisowe.** Przenieść potrzebne komendy starszych `manage_*.py` do podkomend aktywnego narzędzia; zachować obsługę zakończenia starszych tur przed usunięciem dawnych entrypointów.
- [ ] **Weryfikacja korpusu PDF.** Ręcznie sprawdzić pełne materiały, tabele, strony i metadane przed zatwierdzeniem. Dodać przykłady trudnych ekstrakcji do testów.
- [ ] **Budżet kontekstu.** Liczyć całe wejście modelu, łącznie z JSON, promptem i metadanymi; przetestować odrzucenie lub skrócenie zbyt dużego kontekstu.
- [ ] **Większa biblioteka.** Zmierzyć czas wyszukiwania na realistycznym korpusie; dopiero potem dodać cache tokenizacji BM25 i filtrowany indeks wektorowy.
- [ ] **Porządkowanie trwałej historii.** Zdefiniować retencję checkpointów, zdarzeń i konsultacji oraz bezpieczny eksport przed usuwaniem.

## P3 — rozwój produktu

- [ ] **Nowe mechaniki.** Wybrać kolejny zamknięty zakres, np. Help; opisać warunki, koszty, anulowanie i wpływ na stan, dodać kontrakty i testy przed dopuszczeniem decyzji LLM.
- [ ] **Więcej scenariuszy.** Ujednolicić wybór i walidację kart postaci, scenariuszy oraz profili interakcji bez edycji kodu.
- [ ] **Hosting dla wielu graczy.** Jeżeli będzie potrzebny: konta, autoryzacja kampanii, izolacja sekretów, limity zasobów i testy współbieżności przed udostępnieniem usługi w sieci.
