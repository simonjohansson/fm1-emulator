pi32v2_hw = ss.source_set()
pi32v2_hw.add(when: 'CONFIG_FM1_POC', if_true: files('fm1-poc.c', 'fm1-test.c', 'fm1-lcd.c', 'fm1-system.c', 'fm1-nor.c', 'fm1-image.c', 'fm1-usb.c', 'fm1-usb-host.c', 'fm1-alnk.c', 'fm1-audio.c', 'fm1-syscon.c', 'fm1-adc.c', 'fm1-analog.c', 'fm1-input.c', 'fm1-uart.c', 'fm1-sfr.c', 'fm1-crc.c', 'fm1-ttmr.c', 'fm1-lrct.c'))
hw_arch += {'pi32v2': pi32v2_hw}
