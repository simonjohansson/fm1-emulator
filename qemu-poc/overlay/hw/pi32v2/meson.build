pi32v2_hw = ss.source_set()
pi32v2_hw.add(when: 'CONFIG_FM1_POC', if_true: files('fm1-poc.c', 'fm1-lcd.c', 'fm1-system.c', 'fm1-nor.c', 'fm1-usb.c'))
hw_arch += {'pi32v2': pi32v2_hw}
