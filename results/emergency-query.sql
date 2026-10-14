SELECT GEOID,id,name,category,alternate,basic_category,operating_status,confidence,lon,lat,addresses,sources FROM places
      WHERE GEOID IN ('06007001800','06007001900','06007002000','06007002100','06007002200')
        AND (category='emergency_room' OR list_contains(alternate,'emergency_room'))
        AND operating_status='open' AND confidence >= 0.95 ORDER BY id;
