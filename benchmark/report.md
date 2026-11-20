# Run report: dental clinic in Lahore, Pakistan

- Started: 2026-11-18T13:55:43+00:00  |  Finished: 2026-11-18T17:58:26+00:00  |  Wall time this invocation: 336.4s  |  All runs: 1531.6s  |  Resumed runs: 1
- Maps status: **complete**  |  Maps requests (all runs): 504  |  Website pages fetched (this run): 187

## Counts

| Stage | Count |
|---|---|
| unique listings seen (by CID) | 1783 |
| inside city boundary | 1672 |
| matching category | 1642 |
| fuzzy duplicates merged | 44 |
| final businesses | 1598 |
| with phone | 1348 |
| with website | 437 |
| website is social profile | 92 |
| enrich done | 379 |
| enrich failed | 58 |
| with email | 207 |
| total emails | 268 |
| with instagram | 216 |
| with facebook | 195 |
| with linkedin | 35 |
| with whatsapp | 167 |
| email verdict domain accepts mail | 258 |
| email verdict undeliverable | 10 |

## Tiles

```
{
 "dental clinic": {
  "done": 98,
  "split": 14
 },
 "dentist": {
  "done": 12
 }
}
```

## Coverage

- vs `dentist` (12 sampled tiles): main found 212, other found 131, overlap 122, only in other 9. Chapman estimate N~228 -> main-query coverage ~0.932 (upper bound; queries are correlated).

Saturation curve (new unique places per bucket of requests):

| requests | new | cumulative |
|---|---|---|
| 1-50 | 518 | 518 |
| 51-100 | 464 | 982 |
| 101-150 | 372 | 1354 |
| 151-200 | 35 | 1389 |
| 201-250 | 39 | 1428 |
| 251-300 | 96 | 1524 |
| 301-350 | 56 | 1580 |
| 351-400 | 68 | 1648 |
| 401-450 | 94 | 1742 |
| 451-500 | 40 | 1782 |
| 501-502 | 1 | 1783 |

## Failures (111)

- [website] Royal Smile Dental Clinic <https://royalsmileclinics.com/>: PermissionError: disallowed by robots.txt
- [website] Al-Fatima Dental Clinic <http://alfatimadentalclinic.com/>: RetryableError: ConnectError
- [website] Family Dental Clinic <https://familydentalsclinic.com/>: RetryableError: ConnectError
- [website] Sobhi Dental Clinic 100%sterlization <https://sobhidentalsurgery.com/>: RetryableError: ConnectError
- [website] Rehmat Dental Complex <https://rehmatdentalcomplex.com/>: PermissionError: disallowed by robots.txt
- [website] The Dental Co Dental Clinic in DHA Lahore <https://meerhealthcaregroup.com/>: RetryableError: ConnectError
- [website] Jaws and Ivory Dental Clinic <https://jawsandivory.com/>: RetryableError: ConnectError
- [website] Mahmood Dental Care <https://mahmooddentalcare.com/>: RetryableError: ConnectError
- [website] Imran Dental Surgery <http://www.dentalguild.com/>: PermissionError: disallowed by robots.txt
- [website] Wasif Medical Centre and Dental care <http://www.azharwaheed.com/>: RetryableError: ConnectError
- [website] Dental Bay <http://www.thedentalbay.com/>: RetryableError: ConnectError
- [website] Dental Dock |Dr Sehrish | Dental Surgeon | Best Dentist in Walton Lahore | 15 Years Experience | Certified Doctor In Lahore <https://nazirhospital.com/dental-surgeon/>: PermissionError: HTTP 403
- [website] Jasmine Dental Clinic <https://jasminedentalclinics.com/>: RetryableError: HTTP 503
- [website] Sana Samin Dental Care <https://www.sanasamindentalcare.com/>: RetryableError: ConnectError
- [website] Defence Dental Clinic <https://defencedentalclinic.pk/>: RetryableError: ConnectError
- [website] Defence Dental Surgery <http://www.dds.com.pk/>: RetryableError: ConnectError
- [website] bright smile dental clinic <https://www.brightsmiledentalclinic.com/>: RetryableError: ConnectError
- [website] Dental Health Care <http://www.dentalhealthcare6l.com/>: RetryableError: ConnectError
- [website] Sana Dental Aesthetics <https://www.sanadentalaesthetics.com/>: RetryableError: ConnectError
- [website] Dental Services <https://iadsr.edu.pk/dentalservices/>: PermissionError: HTTP 404
- [website] ayazy dental clinic <https://www.ayazydentalclinic.com/>: RetryableError: ConnectError
- [website] Teeth & Co Dental Clinic <https://teethandco.pk/>: RetryableError: ConnectError
- [website] Ali Shams Dental Clinic <https://reliable-biscotti-f8d589.netlify.app/>: PermissionError: HTTP 404
- [website] Smile Matters <https://smilesmatters.com/>: RetryableError: ConnectError
- [website] Xthetica Facial & Dental Aesthetics <http://www.drrubbabzahra.com/>: PermissionError: disallowed by robots.txt
- [website] Premium Smile Dental Care <https://cal.com/premium-smile-dental-care/30>: PermissionError: HTTP 403
- [website] Family Smiles Dental Clinic <https://cal.com/family-smiles-dental-clinic-ook9uh/30mins>: PermissionError: HTTP 403
- [website] Fatima Memorial Dental Hospital <https://www.fatimamemorial.org.pk/>: RetryableError: RemoteProtocolError
- [website] Shahzad Dental Clinic| DR SHAHZAD <http://shahzaddentalclinic.com/>: RetryableError: ConnectError
- [website] Dental Smile <http://www.orthodontist.com.pk/>: RetryableError: ConnectError
- [website] Dental Sense Clinic <http://dentalsense.pk/>: RetryableError: ConnectError
- [website] The Dental Harmony <http://www.thedentalharmony.com/>: RetryableError: HTTP 500
- [website] Oracare Dental Clinic <http://www.oracaredental.net/>: PermissionError: HTTP 404
- [website] Imperial Dental Studio <https://imperialdentalstudio.com/>: RetryableError: ConnectError
- [website] D da dentist <https://ddadentist.com/>: RetryableError: ConnectError
- [website] City Dental hospital <http://www.citydentalhospital.com/>: RetryableError: ConnectError
- [website] Punjab Dental Clinic Shahdara <https://punjabdentalclinics.com/>: RetryableError: ConnectError
- [website] High Class Dental & Medical Division <https://www.highclass.com.pk/>: RetryableError: ConnectError
- [website] Asad Dental Clinic (Shadara Lahore) <http://www.faceboo.com/Dr_Asadullah_Sipra>: PermissionError: disallowed by robots.txt
- [website] Tazeem Dental Care <http://tazeemdental.info/>: RetryableError: ConnectError
- [website] confiDental <http://confidental.com.pk/>: PermissionError: HTTP 403
- [website] Align & Glow Dental Aesthetic Clinic <https://alignandglow.pk/>: RetryableError: ConnectError
- [website] Dr. Sumbal Sahar Rana <https://www.drsumbalsahar.com/>: RetryableError: ConnectError
- [website] Aaqil's Dental Group & Institute <http://www.aaqildental.com/>: RetryableError: ConnectError
- [website] GHC Medical Complex Global Aesthetics Cosmo Derma Institute, Dental & Eye Care Clinic & Institue Health Sciences, Mind Care <http://www.gwopak.org/%20ghccomplex.com.gias.info,%20www.gracerehab.net>: PermissionError: HTTP 403
- [website] Dr. Areeba Imran <https://drareeba.com/>: RetryableError: ConnectError
- [website] Candela Aesthetic and Dental <http://www.candelamedicalcomplex.com/>: RetryableError: ConnectError
- [website] The Dentix & Aesthetic Clinic <https://thedentix.com/>: RetryableError: ConnectError
- [website] Daood Dental Surgery <http://daooddentalsurgery.com/>: RetryableError: ConnectError
- [website] Dental Essence <http://www.dentalessence.pk/>: RetryableError: ConnectError
- [website] Averroysta Dental Healthcare <https://averroysta.com/>: PermissionError: HTTP 401
- [website] Eterna Dental and Aesthetics <https://eternadentalaesthetics.com/>: RetryableError: ConnectError
- [website] Dental & Aesthetic Lounge by XA <https://www.xalounge.com/>: RetryableError: ConnectError
- [website] Royal Smile Dental Clinic <https://royalsmileclinics.com/>: PermissionError: disallowed by robots.txt
- [website] Rehmat Dental Complex <https://rehmatdentalcomplex.com/>: PermissionError: disallowed by robots.txt
- [website] Family Dental Clinic <https://familydentalsclinic.com/>: RetryableError: ConnectError
- [website] Sobhi Dental Clinic 100%sterlization <https://sobhidentalsurgery.com/>: RetryableError: ConnectError
- [website] Al-Fatima Dental Clinic <http://alfatimadentalclinic.com/>: RetryableError: ConnectError
- [website] Imran Dental Surgery <http://www.dentalguild.com/>: PermissionError: disallowed by robots.txt
- [website] The Dental Co Dental Clinic in DHA Lahore <https://meerhealthcaregroup.com/>: RetryableError: ConnectError
- [website] Jaws and Ivory Dental Clinic <https://jawsandivory.com/>: RetryableError: ConnectError
- [website] Dental Bay <http://www.thedentalbay.com/>: RetryableError: ConnectError
- [website] Mahmood Dental Care <https://mahmooddentalcare.com/>: RetryableError: ConnectError
- [website] Wasif Medical Centre and Dental care <http://www.azharwaheed.com/>: RetryableError: ConnectError
- [website] Dental Dock |Dr Sehrish | Dental Surgeon | Best Dentist in Walton Lahore | 15 Years Experience | Certified Doctor In Lahore <https://nazirhospital.com/dental-surgeon/>: PermissionError: HTTP 403
- [website] Defence Dental Clinic <https://defencedentalclinic.pk/>: RetryableError: ConnectError
- [website] Jasmine Dental Clinic <https://jasminedentalclinics.com/>: RetryableError: HTTP 503
- [website] Sana Samin Dental Care <https://www.sanasamindentalcare.com/>: RetryableError: ConnectError
- [website] Defence Dental Surgery <http://www.dds.com.pk/>: RetryableError: ConnectError
- [website] Dental Health Care <http://www.dentalhealthcare6l.com/>: RetryableError: ConnectError
- [website] Ali Shams Dental Clinic <https://reliable-biscotti-f8d589.netlify.app/>: PermissionError: HTTP 404
- [website] Sana Dental Aesthetics <https://www.sanadentalaesthetics.com/>: RetryableError: ConnectError
- [website] Dental Services <https://iadsr.edu.pk/dentalservices/>: PermissionError: HTTP 404
- [website] Teeth & Co Dental Clinic <https://teethandco.pk/>: RetryableError: ConnectError
- [website] Xthetica Facial & Dental Aesthetics <http://www.drrubbabzahra.com/>: PermissionError: disallowed by robots.txt
- [website] bright smile dental clinic <https://www.brightsmiledentalclinic.com/>: RetryableError: ConnectError
- [website] Premium Smile Dental Care <https://cal.com/premium-smile-dental-care/30>: PermissionError: HTTP 403
- [website] Family Smiles Dental Clinic <https://cal.com/family-smiles-dental-clinic-ook9uh/30mins>: PermissionError: HTTP 403
- [website] ayazy dental clinic <https://www.ayazydentalclinic.com/>: RetryableError: ConnectError
- [website] Smile Matters <https://smilesmatters.com/>: RetryableError: ConnectError
- [website] Shahzad Dental Clinic| DR SHAHZAD <http://shahzaddentalclinic.com/>: RetryableError: ConnectError
- [website] Dental Smile <http://www.orthodontist.com.pk/>: RetryableError: ConnectError
- [website] Oracare Dental Clinic <http://www.oracaredental.net/>: PermissionError: HTTP 404
- [website] Fatima Memorial Dental Hospital <https://www.fatimamemorial.org.pk/>: RetryableError: RemoteProtocolError
- [website] Imperial Dental Studio <https://imperialdentalstudio.com/>: RetryableError: ConnectError
- [website] Punjab Dental Clinic Shahdara <https://punjabdentalclinics.com/>: RetryableError: ConnectError
- [website] High Class Dental & Medical Division <https://www.highclass.com.pk/>: RetryableError: ConnectError
- [website] The Dental Harmony <http://www.thedentalharmony.com/>: RetryableError: HTTP 500
- [website] Asad Dental Clinic (Shadara Lahore) <http://www.faceboo.com/Dr_Asadullah_Sipra>: PermissionError: disallowed by robots.txt
- [website] D da dentist <https://ddadentist.com/>: RetryableError: ConnectError
- [website] City Dental hospital <http://www.citydentalhospital.com/>: RetryableError: ConnectError
- [website] Dental Sense Clinic <http://dentalsense.pk/>: RetryableError: ConnectError
- [website] Tazeem Dental Care <http://tazeemdental.info/>: RetryableError: ConnectError
- [website] Dr. Sumbal Sahar Rana <https://www.drsumbalsahar.com/>: RetryableError: ConnectError
- [website] GHC Medical Complex Global Aesthetics Cosmo Derma Institute, Dental & Eye Care Clinic & Institue Health Sciences, Mind Care <http://www.gwopak.org/%20ghccomplex.com.gias.info,%20www.gracerehab.net>: PermissionError: HTTP 403
- [website] Dr. Areeba Imran <https://drareeba.com/>: RetryableError: ConnectError
- [website] Aaqil's Dental Group & Institute <http://www.aaqildental.com/>: RetryableError: ConnectError
- [website] Averroysta Dental Healthcare <https://averroysta.com/>: PermissionError: HTTP 401
- [website] Candela Aesthetic and Dental <http://www.candelamedicalcomplex.com/>: RetryableError: ConnectError
- [website] Daood Dental Surgery <http://daooddentalsurgery.com/>: RetryableError: ConnectError
- [website] Dr.Ashtiaq (Midcity Dental care) <http://night-might.com/>: PermissionError: disallowed by robots.txt
- [website] The Dentix & Aesthetic Clinic <https://thedentix.com/>: RetryableError: ConnectError
- [website] Dental & Aesthetic Lounge by XA <https://www.xalounge.com/>: RetryableError: ConnectError
- [website] Eterna Dental and Aesthetics <https://eternadentalaesthetics.com/>: RetryableError: ConnectError
- [website] Mashhood Dental Care <https://mashhooddentalcare.com/>: RetryableError: ConnectError
- [website] De Dental Pro <http://www.dedentalpro.com.pk/>: RetryableError: ConnectError
- [website] Prime Care Dental, Braces, Implants, Cosmetic and Surgical Dentistry <http://primecaredental.pk/>: RetryableError: ConnectError
- [website] Daood Dental Surgery <http://daooddentalsurgery.com/>: RetryableError: ConnectError
- [website] Align & Glow Dental Aesthetic Clinic <https://alignandglow.pk/>: RetryableError: ConnectError
- [website] Dental Essence <http://www.dentalessence.pk/>: RetryableError: ConnectError
- [website] Pakistan Academy of Implant dentistry <https://mqiclinics.com/>: RetryableError: ConnectError
