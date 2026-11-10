# Synchronisation musicale : diagnostic et validation

## Référence examinée
- Capture 512 échantillons à 44 100 Hz : fenêtre de 11,61 ms (estimation).
- File initiale : 4 blocs, soit jusqu'à environ 46 ms d'audio conservé ; pas une mesure de latence.
- LED à 50 Hz : attente de phase entre 0 et 20 ms, hors envoi.
- Deux enveloppes en cascade, normalisation percentile sans seuil de silence effectif sur le niveau LED.
- Détection historique : axe fréquentiel incorrect (1022 au lieu de 512), réfractaire 180 ms et seuil médian pouvant devenir nul.
- Le BPM est estimé dans un travailleur distinct : il ne donne pas la date des impacts.
- led_wave.py gère le serveur OpenRGB et un ancien effet indépendant ; sa cadence de 29 Hz ne pilote pas led_fans.py.
- Mapping sauvegardé : ventilateurs et boîtier sur le contrôleur principal, clavier séparé. Les ventilateurs partagent leur signal : pas de séparation individuelle garantie.

## Changements
- File bornée au dernier bloc : pas de rattrapage de quatre blocs anciens. Un bloc perdu peut aussi faire perdre un impact ; c'est un compromis de fraîcheur.
- Détecteur causal indépendant du BPM : augmentation d'énergie par bande, seuil absolu, référence adaptative progressive, réfractaire kick 75 ms.
- Les enveloppes lumineuses démarrent sur les événements puis décroissent selon le temps écoulé ; aucune pulsation prédite, aucun second lissage LED dans les nouveaux modes.
- Mode `legacy` : comportement historique disponible (avec correction de l'axe FFT).
- Mode `impacts` par défaut : zones synchronisées sur les kicks.
- Mode `split` : affectations indépendantes kick / snare / high. Les bandes ne constituent pas une séparation de sources : voix, basses et cymbales peuvent contaminer les résultats.
- Sensibilité, intensité, extinction (constante exponentielle en secondes), affectations et mode persistent dans led_config.json via le mécanisme existant.
- Aucun réglage d'anticipation : aucun retard matériel prétendument compensé.

## Vérifications
17 tests existants réussis avant modification ; 22 tests réussis après modification, avec compilation Python.
Commande : python -m unittest test_rhythm_dsp test_bpm_capture test_bpm_latency test_led_pulse test_led_streaming test_led_mapping test_keyboard_brightness
Nouveaux contrôles : impacts isolés et doubles à 120 ms, silence, basse sinusoïdale soutenue, amplitudes faible/forte, extinction. Aucun événement supplémentaire dans ces scénarios ; délai synthétique impact-fin du bloc détecté <24 ms. Ce délai exclut le matériel, le système audio et le rendu LED.
La référence historique ne pouvait pas détecter deux événements séparés de 120 ms avec un réfractaire de 180 ms. Aucun gain physique en millisecondes n'a été mesuré.

## Instrumentation et limites
bpm_source.get_timing() : dernière attente queue_ms depuis la remise du bloc par le callback, dsp_ms de traitement, block_estimate_ms théorique.
led_fans.get_timing() : dernier send_ms des appels OpenRGB et frame_ms du calcul/envoi. Ces durées n'incluent pas de confirmation lumineuse, ni le tampon du pilote, ni le délai acoustique Bluetooth. Les horodatages callback sont logiciels ; ils ne datent pas l'acquisition réelle.
Matériel et écoute non validés. Comparer les trois modes sur le même morceau, filmer son et lumière pour mesurer l'alignement réel, vérifier les breaks et le premier impact, ajuster la sensibilité sans relever le bruit. Tester aussi bruit ambiant, basse modulée, kicks faibles sous une basse forte et morceaux variés : les tests synthétiques ne garantissent pas leur séparation.
Les envois carte/clavier restent séquentiels : un périphérique lent peut encore retarder l'autre. L'instrumentation permettra de vérifier ce point avant d'introduire des travailleurs supplémentaires.
